import { createHash, randomUUID } from 'node:crypto'
import { dirname, isAbsolute } from 'node:path'
import { isDeepStrictEqual } from 'node:util'
import type { Context, Fiber } from '@deepseek-ai/cordis'
import type { Agent, AgentStatus, PreStepDecision } from '@deepseek-ai/dsh-agent'
import type {} from '@deepseek-ai/dsh-commands'
import type {} from '@deepseek-ai/dsh-llm'
import type {} from '@deepseek-ai/dsh-session'
import type { SessionEvent, SessionSeq, UserMessage } from '@deepseek-ai/dsh-session'
import {
  LoopXCliError,
  cliErrorFields,
  coreRouteBin,
  resolveLoopXCommand,
  runFile,
  runJsonCommand,
  supportedLoopXVersion,
} from './cli.ts'
import type { FileRunner, JsonCommandOptions, LoopXCommand } from './cli.ts'
import { resolvePluginLoopXCommand } from './managed-runtime.ts'
import { goalBarCoordinator } from './goalbar/events.ts'
import type {
  GoalBarDriverActionReceipt,
  GoalBarSessionCapture,
} from './goalbar/events.ts'
import type { GoalBarExpectedBindingV1 } from './goalbar/protocol.ts'
import { GOALBAR_ADMISSION_CLEAR } from './goalbar/protocol.ts'
import type {
  GoalBarAdmissionFailureCategoryV1,
  GoalBarContinuationAdmissionV1,
} from './goalbar/protocol.ts'
import {
  applyTodoUnitFoldAndPrefetch,
  createDefaultDshObsRunner,
  decodeQuotaSettlementReadback,
  extractTodoIdFromQuotaAndHeartbeat,
  hasValidDshToolCallPairing,
  recordDshTodoUnitAnchor,
  validateTodoUnitObsForExecution,
} from './todo-unit-fold.ts'
import type {
  LoopXTodoFoldMessageSource,
  TodoUnitAnchor,
  TodoUnitAnchorInput,
  TodoUnitCompletionReceipt,
  TodoUnitFoldOutcome,
  TodoUnitFoldPolicy,
  TodoUnitFoldRequest,
  TodoUnitObsNextCommand,
  TodoUnitObsRunner,
  TodoUnitReceiptMu,
  TodoUnitSourceRef,
} from './todo-unit-fold.ts'

export type {
  LoopXTodoFoldMessageSource,
  TodoUnitAnchor,
  TodoUnitAnchorInput,
  TodoUnitCompletionReceipt,
  TodoUnitFoldOutcome,
  TodoUnitFoldPolicy,
  TodoUnitFoldRequest,
  TodoUnitObsNextCommand,
  TodoUnitObsRunner,
  TodoUnitReceiptMu,
  TodoUnitSourceRef,
}

export const name = 'dsh-loopx-driver'
export const inject = ['agents', 'loopxBootstrap']

const HOST_SURFACE = 'deepseek-harness-native'
const RESOLUTION_SCHEMA = 'loopx_thread_agent_binding_resolution_v0'
const HEARTBEAT_SCHEMA = 'heartbeat_agent_input_v1'
const CONTINUATION_SCHEMA = 'loopx_dsh_continuation_v0'
const RECEIPT_READBACK_SCHEMA = 'quota_receipt_readback_v0'
const DEFAULT_WAIT_MS = 5 * 60_000
const MAX_WAIT_MS = 24 * 60 * 60_000
const SELECTION_RECOVERY_COMPLETION_LIMIT = 2

type TimerHandle = unknown
type ReservationPhase = 'queued' | 'claimed' | 'admitted' | 'retired'

export interface DriverClock {
  setTimeout(callback: () => void, delayMs: number): TimerHandle
  clearTimeout(handle: TimerHandle): void
}

interface Binding {
  readonly goalId: string
  readonly agentId: string
}

interface SelectionTurn extends Binding {
  readonly turnInstanceId: string
}

interface SelectionRecovery extends SelectionTurn {
  readonly completedAttempts: number
}

interface LoopXContinuationMessageSource {
  readonly kind: 'loopx-continuation'
  readonly schemaVersion: typeof CONTINUATION_SCHEMA
  readonly goalId: string
  readonly agentId: string
  readonly turnInstanceId: string
  readonly selectionRequired?: true
}

declare module '@deepseek-ai/dsh-llm' {
  interface MessageSourceMap {
    'loopx-continuation': LoopXContinuationMessageSource
  }
}

interface Reservation extends Binding {
  readonly session: Agent['session']
  readonly messageId: UserMessage['id']
  readonly content: UserMessage['content']
  readonly source: LoopXContinuationMessageSource
  readonly turnInstanceId: string
  phase: ReservationPhase
  /** Exact reservation already claimed/admitted when GoalBar Pause retired future work. */
  preserveAcrossPause: boolean
}

interface ActiveTodoUnitState {
  readonly goalId: string
  readonly agentId: string
  readonly todoId?: string | undefined
  readonly turnInstanceId: string
  readonly contextEpoch: string
  readonly startStepIndex: number
  endStepIndex: number
  anchor?: TodoUnitAnchor | undefined
  admittedSeq?: SessionSeq | undefined
  completedCleanly?: boolean | undefined
}

interface SessionDriverRetention {
  readonly agent: Agent
  paused: boolean
  goalBarPaused?: boolean
  admissionFailure?: GoalBarAdmissionFailureCategoryV1 | undefined
  reservation?: Reservation | undefined
  todoUnitContextEpoch?: string | undefined
  todoUnitStepCount?: number | undefined
  activeTodoUnit?: ActiveTodoUnitState | undefined
  todoUnitAnchors?: Map<string, TodoUnitAnchor> | undefined
  todoUnitFolds?: Map<string, TodoUnitFoldOutcome> | undefined
  todoUnitFoldsInFlight?: Map<string, Promise<TodoUnitFoldOutcome>> | undefined
}

// HMR can replace this module while the same Agent and Session keep running.
// Retain cancellation ownership and pause/readback state. Core
// remains the owner of admission and settlement. Weak keys do not retain closed Sessions.
const retentionKey = Symbol.for('loopx.dsh.session-driver-retention.v1')
const retainedHost = globalThis as typeof globalThis & {
  [retentionKey]?: WeakMap<Agent['session'], SessionDriverRetention>
}
const sessionRetention = retainedHost[retentionKey] ??= new WeakMap()

function retainedState(agent: Agent, session: Agent['session']): SessionDriverRetention {
  let retained = sessionRetention.get(session)
  if (retained?.agent !== agent) {
    retained = { agent, paused: sessionErrorPause(session) }
    sessionRetention.set(session, retained)
  }
  return retained
}

interface ActivationProjection {
  readonly session: Agent['session']
  readonly pendingModelCalls: Set<string>
  activated: boolean
  hostTurn?: { turn: number; entered: boolean; selection?: SelectionTurn } | undefined
  pendingSelection?: SelectionRecovery | undefined
}

interface EvaluationWaiter {
  readonly session: Agent['session']
  readonly resolve: (evaluated: boolean) => void
}

interface DriverState {
  readonly agent: Agent
  activation: ActivationProjection
  requested: boolean
  stopping: boolean
  competing: boolean
  readonly pauseAfterTurnError: boolean
  schedulerToken: string
  unchangedPolls: number
  readonly commands: Set<string>
  readonly evaluationWaiters: Set<EvaluationWaiter>
  run?: Promise<void> | undefined
  controller?: AbortController | undefined
  timer?: TimerHandle | undefined
  reservation?: Reservation | undefined
  recoveryAttention?: Binding | undefined
  retrySelection: boolean
}

interface QueuePlan extends Binding {
  readonly kind: 'queue'
  readonly session: Agent['session']
  readonly turnInstanceId: string
  readonly taskBody: string
  readonly selectionRequired: boolean
  readonly todoId?: string | undefined
}

interface TodoUnitPrefetchPin {
  readonly session: Agent['session']
  readonly activationSession: Agent['session']
  readonly activeUnit: ActiveTodoUnitState | undefined
  readonly anchor: TodoUnitAnchor | undefined
  readonly storedAnchor: TodoUnitAnchor | undefined
  readonly stepCount: number
  readonly nodes: readonly SessionSeq[]
  readonly tailSeqs: readonly SessionSeq[]
  readonly eventCount: number
  readonly lastEventSeq: number
}

function isTodoUnitDriverQuiescent(agent: Agent, state: DriverState): boolean {
  return agent.status === 'idle'
    && !state.competing
    && state.commands.size === 0
    && !inboxHasPending(agent.inbox)
    && state.activation.pendingModelCalls.size === 0
    && agent.id === agent.session.id
    && agent.id === agent.session.header.id
}

function captureTodoUnitPrefetchPin(
  agent: Agent,
  state: DriverState,
  retained: SessionDriverRetention,
  todoId: string,
  anchor: TodoUnitAnchor | undefined,
): TodoUnitPrefetchPin {
  const session = agent.session
  const nodes = [...session.surface.nodes]
  const events = session.snapshotEvents()
  const prefixCount = anchor?.prefixNodeCount ?? 0
  return {
    session,
    activationSession: state.activation.session,
    activeUnit: retained.activeTodoUnit,
    anchor,
    storedAnchor: todoId ? retained.todoUnitAnchors?.get(todoId) : undefined,
    stepCount: retained.todoUnitStepCount ?? 0,
    nodes,
    tailSeqs: nodes.slice(prefixCount),
    eventCount: events.length,
    lastEventSeq: Number(events.at(-1)?.seq ?? 0),
  }
}

function detectTodoUnitPrefetchInterference(
  driverDisposed: boolean,
  agent: Agent,
  state: DriverState,
  retained: SessionDriverRetention,
  todoId: string,
  anchorOverride: TodoUnitAnchor | undefined,
  pin: TodoUnitPrefetchPin,
): string | null {
  if (
    driverDisposed || state.stopping || agent.session !== pin.session
    || state.activation.session !== pin.activationSession
    || agent.id !== agent.session.id || agent.id !== agent.session.header.id
  ) {
    return 'session_changed_during_prefetch'
  }
  if (retained.activeTodoUnit !== pin.activeUnit) {
    return 'active_unit_changed_during_prefetch'
  }
  const currentStoredAnchor = todoId ? retained.todoUnitAnchors?.get(todoId) : undefined
  const currentAnchor = anchorOverride ?? currentStoredAnchor
  if (
    currentAnchor !== pin.anchor
    || currentStoredAnchor !== pin.storedAnchor
    || (retained.todoUnitStepCount ?? 0) !== pin.stepCount
  ) {
    return 'segment_advanced_during_prefetch'
  }
  const events = pin.session.snapshotEvents()
  const newEvents = events.filter((ev, idx) => idx >= pin.eventCount || Number(ev.seq) > pin.lastEventSeq)
  if (newEvents.some(ev => ev.type === 'user/message')) {
    return 'user_message_during_prefetch'
  }
  if (newEvents.some(ev => (
    ev.type === 'step/start' || ev.type === 'turn/start'
    || ev.type === 'assistant/message' || ev.type === 'tool/call' || ev.type === 'tool/result'
  ))) {
    return 'segment_advanced_during_prefetch'
  }
  const liveNodes = pin.session.surface.nodes
  if (liveNodes.length !== pin.nodes.length || liveNodes.some((seq, idx) => seq !== pin.nodes[idx])) {
    return 'tail_changed_during_prefetch'
  }
  if (!isTodoUnitDriverQuiescent(agent, state)) {
    return 'non_quiescent_boundary'
  }
  return null
}

interface WaitPlan {
  readonly kind: 'wait'
  readonly delayMs?: number | undefined
  readonly schedulerToken?: string | undefined
  readonly unchangedPolls?: number | undefined
  readonly recoveryAttention?: Binding | undefined
}

type EvaluationPlan = QueuePlan | WaitPlan
type AdmissionStage = 'resolve-command' | 'resolve-binding' | 'quota' | 'heartbeat'
  | 'pre-step-recheck' | 'maintenance-slot'

function preStepRejection(
  reason: string, messages: UserMessage[], submitted: UserMessage, startedAt: number,
): string {
  const kinds = [...new Set(messages.filter(message => message.id !== submitted.id).map(message => {
    const kind = message.source?.kind
    return typeof kind === 'string' && /^[a-z][a-z0-9-]{0,47}$/u.test(kind) ? kind : 'unknown'
  }))].sort().slice(0, 16)
  return `pre_step_rejected stage=pre-step-recheck reason=${reason} other_kinds=${kinds.join(',') || 'none'} elapsed_ms=${Math.round(performance.now() - startedAt)}`
}

/**
 * Global Core route options that pin Core-rendered continuation text (task
 * bodies, settlement command templates) to the same executable this adapter
 * probed, instead of whatever bare `loopx` resolves to on the agent's PATH.
 */
function coreRouteArgs(command: LoopXCommand): string[] {
  const route = coreRouteBin(command)
  return route === undefined ? [] : ['--cli-bin', route]
}

export interface LoopXDriverOptions {
  readonly isLiveAgent: (agent: Agent) => boolean
  readonly runner?: FileRunner | undefined
  readonly resolveCommand?: (
    signal: AbortSignal,
  ) => Promise<LoopXCommand>
  readonly clock?: DriverClock | undefined
  readonly makeTurnInstanceId?: (() => string) | undefined
  readonly retryDelaysMs?: readonly number[] | undefined
  readonly runDetached?: (<T>(operation: () => Promise<T>) => Promise<T>) | undefined
  readonly warn?: ((message: string) => void) | undefined
  /** Notified only when the projected admission failure is set or cleared. */
  readonly onAdmissionChange?: ((session: Agent['session']) => void) | undefined
  /** Strictly default-off Todo semantic-unit context folding & prefetch policy. */
  readonly todoUnitFold?: TodoUnitFoldPolicy | undefined
  /** Optional bounded reader for Todo-unit Obs(next_cmd) verification commands. */
  readonly runObsCommand?: TodoUnitObsRunner | undefined
}

const systemClock: DriverClock = Object.freeze({
  setTimeout(callback: () => void, delayMs: number) {
    return setTimeout(callback, delayMs)
  },
  clearTimeout(handle: TimerHandle) {
    clearTimeout(handle as ReturnType<typeof setTimeout>)
  },
})

function continuationSource(
  message: UserMessage,
): LoopXContinuationMessageSource | undefined {
  const observed = record(message.source)
  const source = exactRecord(observed, [
    'kind',
    'schemaVersion',
    'goalId',
    'agentId',
    'turnInstanceId',
    ...(observed?.selectionRequired === true ? ['selectionRequired'] : []),
  ])
  return source?.kind === 'loopx-continuation'
    && source.schemaVersion === CONTINUATION_SCHEMA
    && typeof source.goalId === 'string'
    && source.goalId.length > 0
    && typeof source.agentId === 'string'
    && source.agentId.length > 0
    && typeof source.turnInstanceId === 'string'
    && source.turnInstanceId.length > 0
    ? source as unknown as LoopXContinuationMessageSource
    : undefined
}

function isDriverMessage(message: UserMessage): boolean {
  // A malformed continuation still belongs to the Driver. Only
  // sameReservation can admit its payload to a model step.
  return message.source?.kind === 'loopx-continuation'
}

function otherStepInputs(messages: readonly UserMessage[], submitted: UserMessage): UserMessage[] {
  return messages.filter(message => message.id !== submitted.id && !isDriverMessage(message))
}

function isHostContextMessage(message: UserMessage): boolean {
  // DSH kind identifies the producer; snapshot/catalog forms also describe plugin messages.
  const kind: string = message.source.kind
  return kind === 'runtime-context' || kind === 'time-context' || kind === 'skill-catalog'
}

function sameReservation(message: UserMessage, reservation: Reservation): boolean {
  const source = continuationSource(message)
  return message.id === reservation.messageId
    && source !== undefined
    && isDeepStrictEqual(source, reservation.source)
    && isDeepStrictEqual(message.content, reservation.content)
}

function automaticMessage(
  plan: QueuePlan,
): UserMessage & { readonly source: LoopXContinuationMessageSource } {
  const content: UserMessage['content'] = Object.freeze([
    Object.freeze({ type: 'text' as const, text: plan.taskBody }),
  ]) as UserMessage['content']
  return Object.freeze({
    id: randomUUID() as UserMessage['id'],
    role: 'user' as const,
    content,
    source: Object.freeze({
      kind: 'loopx-continuation' as const,
      schemaVersion: CONTINUATION_SCHEMA,
      goalId: plan.goalId,
      agentId: plan.agentId,
      turnInstanceId: plan.turnInstanceId,
      ...(plan.selectionRequired ? { selectionRequired: true as const } : {}),
    }),
  })
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined
}

function exactRecord(
  value: unknown,
  expectedKeys: readonly string[],
): Record<string, unknown> | undefined {
  const candidate = record(value)
  if (candidate === undefined) return undefined
  const keys = Reflect.ownKeys(candidate)
  if (keys.some(key => typeof key !== 'string') || keys.length !== expectedKeys.length) {
    return undefined
  }
  const allowed = new Set(expectedKeys)
  return keys.every(key => typeof key === 'string' && allowed.has(key))
    ? candidate
    : undefined
}

function eventCallId(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

function exactUserActivation(event: SessionEvent): boolean {
  if (event.type !== 'user/message') return false
  const data = record(event.data)
  const source = record(data?.source)
  return source?.kind === 'skill-invocation'
    && source.name === 'loopx'
    && source.form === 'instructions'
}

function exactModelActivationCall(event: SessionEvent): string | undefined {
  if (event.type !== 'tool/call') return undefined
  const data = record(event.data)
  const callId = eventCallId(data?.callId)
  if (callId === undefined || data?.name !== 'skill'
    || typeof data.arguments !== 'string') return undefined
  let parsed: unknown
  try {
    parsed = JSON.parse(data.arguments)
  } catch {
    return undefined
  }
  return record(parsed)?.name === 'loopx' ? callId : undefined
}

function foldActivationEvent(
  projection: ActivationProjection,
  event: SessionEvent,
): void {
  if (projection.activated) return
  if (exactUserActivation(event)) {
    projection.activated = true
    projection.pendingModelCalls.clear()
    return
  }
  if (event.type === 'tool/call') {
    const data = record(event.data)
    const callId = eventCallId(data?.callId)
    if (callId === undefined) return
    projection.pendingModelCalls.delete(callId)
    if (exactModelActivationCall(event) !== undefined) {
      projection.pendingModelCalls.add(callId)
    }
    return
  }
  if (event.type !== 'tool/result') return
  const data = record(event.data)
  const message = record(data?.message)
  const source = record(message?.source)
  const callId = eventCallId(source?.callId)
  if (callId === undefined) return
  const matched = projection.pendingModelCalls.delete(callId)
  const content = message?.content
  const block = Array.isArray(content) && content.length === 1
    ? record(content[0])
    : undefined
  // DSH 0.2.2 records a tool result as a tool-role message whose call id and
  // error flag sit on the message; older hosts used a user-role message
  // carrying one tool-result block. Accept both.
  const legacyResult = message?.role === 'user'
    && block?.type === 'tool-result'
    && block.toolCallId === callId
    && (block.isError === undefined || block.isError === false)
  const toolRoleResult = message?.role === 'tool'
    && message.toolCallId === callId
    && (message.isError === undefined || message.isError === false)
  const successful = source?.kind === 'tool'
    && (legacyResult || toolRoleResult)
    && data?.error === undefined
  if (matched && successful) {
    projection.activated = true
    projection.pendingModelCalls.clear()
  }
}

function foldSelectionEvent(projection: ActivationProjection, event: SessionEvent): void {
  if (event.type === 'turn/start') {
    projection.hostTurn = { turn: event.data.turn, entered: false }
    return
  }
  const hostTurn = projection.hostTurn
  if (hostTurn === undefined) return
  if (event.type === 'user/message') {
    const source = continuationSource(event.data)
    if (source?.selectionRequired === true) {
      hostTurn.selection = { goalId: source.goalId, agentId: source.agentId,
        turnInstanceId: source.turnInstanceId }
    }
  } else if (event.type === 'step/start' && hostTurn.turn === event.data.turn) {
    hostTurn.entered = true
  } else if (event.type === 'turn/end' && hostTurn.turn === event.data.turn) {
    projection.hostTurn = undefined
    if (event.data.reason.kind === 'completed' && hostTurn.entered && hostTurn.selection !== undefined) {
      const selection = hostTurn.selection
      const previous = projection.pendingSelection
      const sameOriginal = previous?.turnInstanceId === selection.turnInstanceId
        && previous.goalId === selection.goalId && previous.agentId === selection.agentId
      projection.pendingSelection = {
        ...selection,
        completedAttempts: sameOriginal ? previous.completedAttempts + 1 : 1,
      }
    }
  }
}

function foldSessionActivation(session: Agent['session']): ActivationProjection {
  const projection: ActivationProjection = {
    session,
    pendingModelCalls: new Set(),
    activated: false,
  }
  try {
    for (const event of session.snapshotEvents()) {
      foldActivationEvent(projection, event)
      foldSelectionEvent(projection, event)
    }
  } catch {
    projection.pendingModelCalls.clear()
    projection.activated = false
    projection.hostTurn = undefined
    projection.pendingSelection = undefined
  }
  return projection
}

/** Reopening a Session must not erase its last typed failure boundary. */
function sessionErrorPause(session: Agent['session']): boolean {
  let paused = false
  let driverTurn = false
  try {
    for (const event of session.snapshotEvents()) {
      if (event.type === 'turn/start') driverTurn = false
      if (event.type === 'user/message') {
        if (event.data.source.kind === 'user') paused = false
        if (continuationSource(event.data) !== undefined) driverTurn = true
      }
      if (event.type !== 'turn/end') continue
      // A later admitted LoopX Turn records successful recovery. Completing
      // unrelated automatic input is not permission to resume LoopX.
      if (event.data.reason.kind === 'completed' && driverTurn) paused = false
      if (event.data.reason.kind === 'aborted' || event.data.reason.kind === 'max-tokens') paused = true
      driverTurn = false
    }
  } catch {
    return true
  }
  return paused
}

/**
 * Whether an Agent still holds input it has not consumed. The 0.1.5 Inbox
 * exposes the two pending queues instead of the retired `hasPending` flag.
 */
function inboxHasPending(inbox: Agent['inbox']): boolean {
  return inbox.nextTurn.length > 0 || inbox.nextStep.length > 0
}

/** Pending context or another automatic message cannot release a rejected step. */
function inboxHasIndependentInput(inbox: Agent['inbox']): boolean {
  return [...inbox.nextTurn, ...inbox.nextStep].some(message => (
    !isDriverMessage(message) && !isHostContextMessage(message)
  ))
}

function inboxContains(inbox: Agent['inbox'], id: UserMessage['id']): boolean {
  return inbox.nextStep.some(message => message.id === id)
    || inbox.nextTurn.some(message => message.id === id)
}

function exactBinding(
  payload: Record<string, unknown>,
  threadId: string,
): Binding | undefined {
  if (payload.schema_version !== RESOLUTION_SCHEMA
    || payload.ok !== true
    || payload.status !== 'bound'
    || payload.host_surface !== HOST_SURFACE
    || payload.thread_id !== threadId) return undefined
  const goalId = typeof payload.goal_id === 'string' ? payload.goal_id : ''
  const agentId = typeof payload.agent_id === 'string' ? payload.agent_id : ''
  const matches = Array.isArray(payload.matches) ? payload.matches : []
  const match = matches.length === 1 ? record(matches[0]) : undefined
  return goalId.length > 0
    && goalId.trim() === goalId
    && agentId.length > 0
    && agentId.trim() === agentId
    && match?.goal_id === goalId
    && match.agent_id === agentId
    ? { goalId, agentId }
    : undefined
}

const QUOTA_NAVIGATION_SCHEMA = 'context_capture_navigation_v1'
const QUOTA_SELECTION_PAGE_SCHEMA = 'context_capture_selection_page_v1'
const QUOTA_INLINE_PAGE_SCHEMA = 'context_capture_page_v1'
const QUOTA_INDEX_SCHEMA = 'context_capture_index_v1'
const QUOTA_SELECTION_SCHEMA = 'context_capture_selection_v1'
const QUOTA_REQUIRED_POINTERS = ['/ok', '/mode', '/goal_id', '/should_run', '/agent_identity']
const QUOTA_RECEIPT_POINTER = '/heartbeat_receipt'
const QUOTA_OPTIONAL_POINTERS = [
  QUOTA_RECEIPT_POINTER, '/scheduler_hint', '/reward_memory_recall',
  '/unsettled_host_turn_recovery',
]

type QuotaReadOptions = Pick<JsonCommandOptions, 'runner' | 'cwd' | 'signal' | 'attempts' | 'retryDelaysMs'>

function quotaNavigationReference(payload: Record<string, unknown>): string | undefined {
  if (payload.schema_version !== QUOTA_NAVIGATION_SCHEMA || payload.ok !== true) return undefined
  const path = record(payload.recovery)?.reference_file
  return typeof path === 'string' && isAbsolute(path) && !path.includes('\0') ? path : undefined
}

async function readQuotaSelections(
  command: LoopXCommand,
  reference: string,
  selectionArgs: readonly string[],
  schema: string,
  options: QuotaReadOptions,
): Promise<unknown[]> {
  let cursor = reference
  let offset = 0
  let total: number | undefined
  let inlineDigests: { source: string; selection: string } | undefined
  const parts: string[] = []
  const isListing = schema === QUOTA_INDEX_SCHEMA
  const pointers = isListing ? [''] : selectionArgs.filter((_, index) => index % 2 === 1)
  for (;;) {
    const page = await runJsonCommand(command, [
      '--format', 'json', 'quota', 'context-read', '--reference-file', cursor,
      '--offset', String(offset), '--page-bytes', '64000', '--max-page-output-bytes', '64000',
      ...(offset === 0 || inlineDigests !== undefined ? selectionArgs : []),
      ...(inlineDigests === undefined ? [] : ['--selection-sha256', inlineDigests.selection]),
    ], options)
    if (page.ok !== true
      || (page.schema_version !== QUOTA_SELECTION_PAGE_SCHEMA && page.schema_version !== QUOTA_INLINE_PAGE_SCHEMA)
      || typeof page.content !== 'string' || page.offset !== offset
      || typeof page.total_bytes !== 'number' || !Number.isSafeInteger(page.total_bytes)
      || page.total_bytes <= 0
      || (total !== undefined && page.total_bytes !== total)) {
      throw new LoopXCliError('invalid_schema', 'LoopX quota capture returned an invalid page', false)
    }
    if (page.schema_version === QUOTA_INLINE_PAGE_SCHEMA) {
      const { sha256: source, selection_sha256: selection, selected_pointers: selected } = page
      if (typeof source !== 'string' || !/^[a-f0-9]{64}$/u.test(source)
        || typeof selection !== 'string' || !/^[a-f0-9]{64}$/u.test(selection)
        || (page.list_fields === true) !== isListing
        || !Array.isArray(selected) || selected.length !== pointers.length
        || !pointers.every(pointer => selected.includes(pointer))
        || (inlineDigests === undefined && offset !== 0)
        || (inlineDigests !== undefined && (source !== inlineDigests.source || selection !== inlineDigests.selection))) {
        throw new LoopXCliError('invalid_schema', 'LoopX quota capture returned an invalid inline selection', false)
      }
      // A read-only capture may have no derived reader descriptor. Continue
      // through the original reference with the same selector and view hash.
      inlineDigests = { source, selection }
    }
    total = page.total_bytes
    const next = offset + Buffer.byteLength(page.content, 'utf8')
    parts.push(page.content)
    if (page.eof === true && next === total && page.next_offset === null) break
    const nextReference = inlineDigests === undefined ? page.reference_file : reference
    if (page.eof !== false || next <= offset || next >= total || page.next_offset !== next
      || typeof nextReference !== 'string' || !isAbsolute(nextReference)
      || nextReference.includes('\0') || dirname(nextReference) !== dirname(reference)
      || (offset !== 0 && nextReference !== cursor)) {
      throw new LoopXCliError('invalid_schema', 'LoopX quota capture returned an invalid continuation', false)
    }
    cursor = nextReference
    offset = next
  }
  const selectedBody = parts.join('')
  if (inlineDigests !== undefined
    && createHash('sha256').update(selectedBody, 'utf8').digest('hex') !== inlineDigests.selection) {
    throw new LoopXCliError('invalid_schema', 'LoopX quota capture selection digest did not match its bytes', false)
  }
  let body: Record<string, unknown> | undefined
  try {
    body = record(JSON.parse(selectedBody))
  } catch {
    throw new LoopXCliError('invalid_json', 'LoopX quota capture returned invalid selection JSON', false)
  }
  if (body?.schema_version !== schema || !Array.isArray(body.selections)) {
    throw new LoopXCliError('invalid_schema', 'LoopX quota capture returned an incompatible selection', false)
  }
  return body.selections
}

async function readQuotaCapture(
  command: LoopXCommand,
  reference: string,
  options: QuotaReadOptions,
  revalidateOnly: boolean,
  includeSelectedTodo = false,
): Promise<Record<string, unknown>> {
  // Entering a queued step requires the original Turn receipt. The scheduler
  // and reward memory are only used when first deciding whether to queue work.
  let pointers = [...QUOTA_REQUIRED_POINTERS, QUOTA_RECEIPT_POINTER]
  if (!revalidateOnly) {
    // The ordinary selector rejects missing paths. Discover which optional
    // fields exist on the same verified capture before preparing the step.
    const index = await readQuotaSelections(command, reference, ['--list'], QUOTA_INDEX_SCHEMA, options)
    const root = record(index[0])
    if (index.length !== 1 || root?.pointer !== '' || !Array.isArray(root.fields)) {
      throw new LoopXCliError('invalid_schema', 'LoopX quota capture returned an invalid field index', false)
    }
    const present = new Set(root.fields.map(field => record(field)?.pointer))
    if (!QUOTA_REQUIRED_POINTERS.every(pointer => present.has(pointer))) {
      throw new LoopXCliError('invalid_schema', 'LoopX quota capture lacks admission fields', false)
    }
    const optional = includeSelectedTodo
      ? [...QUOTA_OPTIONAL_POINTERS, '/selected_todo']
      : QUOTA_OPTIONAL_POINTERS
    pointers = [...QUOTA_REQUIRED_POINTERS, ...optional.filter(pointer => present.has(pointer))]
  }
  const selected = await readQuotaSelections(
    command, reference, pointers.flatMap(pointer => ['--select', pointer]), QUOTA_SELECTION_SCHEMA, options,
  )
  const result: Record<string, unknown> = {}
  const remaining = new Set(pointers)
  for (const item of selected) {
    const field = record(item)
    if (typeof field?.pointer !== 'string' || !remaining.delete(field.pointer) || !Object.hasOwn(field, 'value')) {
      throw new LoopXCliError('invalid_schema', 'LoopX quota capture returned an unexpected admission field', false)
    }
    result[field.pointer.slice(1)] = field.value
  }
  if (remaining.size !== 0) {
    throw new LoopXCliError('invalid_schema', 'LoopX quota capture omitted requested admission fields', false)
  }
  return result
}

function exactQuotaContext(
  payload: Record<string, unknown>,
  binding: Binding,
): boolean {
  const identity = record(payload.agent_identity)
  return payload.mode === 'should-run'
    && payload.goal_id === binding.goalId
    && identity?.agent_id === binding.agentId
    && identity.registered === true
}

function exactQuotaReceipt(
  payload: Record<string, unknown>,
  turnInstanceId: string,
): boolean {
  return record(payload.heartbeat_receipt)?.turn_instance_id === turnInstanceId
}

function exactQuota(
  payload: Record<string, unknown>,
  binding: Binding,
  turnInstanceId: string,
): boolean {
  return exactQuotaContext(payload, binding)
    && payload.ok === true
    && payload.should_run === true
    && exactQuotaReceipt(payload, turnInstanceId)
}

/** Core's own typed failure to persist this exact Turn's receipt; the next evaluation may retry. */
function receiptWriteFailed(payload: Record<string, unknown>, turnInstanceId: string): boolean {
  const receipt = record(payload.heartbeat_receipt)
  return payload.effective_action === 'heartbeat_receipt_write_failed'
    && receipt?.status === 'write_failed' && receipt.turn_instance_id === turnInstanceId
}

function schedulerWait(
  payload: Record<string, unknown>,
  currentToken: string,
  currentPolls: number,
): WaitPlan {
  const scheduler = record(payload.scheduler_hint)
  const unchanged = record(scheduler?.unchanged_poll)
  const local = record(record(scheduler?.cold_path_detail)?.local_scheduler)
  if (local === undefined) return { kind: 'wait' }

  const reset = record(scheduler?.reset_policy)
  const token = typeof reset?.reset_token === 'string' ? reset.reset_token : ''
  const unchangedPolls = token.length > 0 && token === currentToken
    ? currentPolls
    : 0
  const rawLimit = record(unchanged?.limits)?.local_scheduler
  const limit = typeof rawLimit === 'number'
    && Number.isSafeInteger(rawLimit)
    && rawLimit >= 0
    ? rawLimit
    : undefined
  if (limit !== undefined && unchangedPolls >= limit) {
    return { kind: 'wait', schedulerToken: token, unchangedPolls }
  }

  const progression = Array.isArray(local.example_progression_minutes)
    ? local.example_progression_minutes.filter((value): value is number => (
        typeof value === 'number' && Number.isFinite(value) && value > 0
      ))
    : []
  const recommended = local.recommended_interval_minutes
  const fallback = typeof recommended === 'number'
    && Number.isFinite(recommended)
    && recommended > 0
    ? recommended
    : DEFAULT_WAIT_MS / 60_000
  const minutes = progression.length > 0
    ? progression[Math.min(unchangedPolls, progression.length - 1)] ?? fallback
    : fallback
  let delayMs = minutes * 60_000
  const recheck = local.recheck_after_seconds
  if (typeof recheck === 'number' && Number.isFinite(recheck) && recheck >= 0) {
    delayMs = Math.min(delayMs, recheck * 1_000)
  }
  return {
    kind: 'wait',
    delayMs: Math.min(Math.round(delayMs), MAX_WAIT_MS),
    schedulerToken: token,
    unchangedPolls: unchangedPolls + 1,
  }
}

function exactHeartbeat(
  payload: Record<string, unknown>,
  binding: Binding,
  turnInstanceId: string,
): string | undefined {
  if (payload.schema_version !== HEARTBEAT_SCHEMA
    || payload.ok !== true
    || payload.goal_id !== binding.goalId
    || payload.agent_id !== binding.agentId
    || payload.turn_instance_id !== turnInstanceId) return undefined
  const taskBody = typeof payload.task_body === 'string' ? payload.task_body : ''
  const assignments = [...taskBody.matchAll(/\bLOOPX_TURN=([^\s`'";]+)|\$LOOPX_TURN\s*=\s*"([^\s`'";]+)"/gu)]
    .map(match => match[1] ?? match[2])
  return taskBody.length > 0
    && taskBody.length <= 32_000
    && assignments.length === 1
    && assignments[0] === turnInstanceId
    ? taskBody
    : undefined
}

function taskBodyWithRewardMemory(
  taskBody: string,
  quota: Record<string, unknown>,
): string {
  const recall = record(quota.reward_memory_recall)
  const context = record(recall?.context)
  const rawGuidance = Array.isArray(context?.guidance) ? context.guidance : []
  const guidance = rawGuidance.flatMap(item => {
    const value = record(item)
    const candidateRef = typeof value?.candidate_ref === 'string'
      ? value.candidate_ref : ''
    const targetClass = typeof value?.target_class === 'string'
      ? value.target_class : ''
    const contentSummary = typeof value?.content_summary === 'string'
      ? value.content_summary : ''
    return candidateRef.length > 0
      && candidateRef.length <= 160
      && targetClass.length > 0
      && targetClass.length <= 80
      && contentSummary.length > 0
      && contentSummary.length <= 500
      ? [{ candidate_ref: candidateRef, target_class: targetClass,
        content_summary: contentSummary }]
      : []
  }).slice(0, 4)
  if (guidance.length === 0) return taskBody
  const packet = JSON.stringify({
    schema_version: 'loopx_turn_reward_memory_context_v0',
    authority: 'guidance_only',
    instruction: 'Use only when consistent with current evidence; this grants no action authority.',
    guidance,
  })
  const suffix = `\n\nLoopX private Reward Memory context:\n${packet}`
  return Buffer.byteLength(suffix, 'utf8') <= 4_096
      && Buffer.byteLength(taskBody + suffix, 'utf8') <= 32_000
    ? taskBody + suffix
    : taskBody
}

function renderThrown(value: unknown): string {
  if (value instanceof LoopXCliError) return `${value.kind}:${value.message}`
  return value instanceof Error ? value.message : String(value)
}

/**
 * Typed unload cause for {@link LoopXContinuationDriver.dispose}.
 *
 * - `removal` — user disable/uninstall, entry removal, or host cascade
 *   disposal: claimed/admitted running automatic work is cancelled.
 * - `replacement` — plugin-tree recomposition within a live host (module
 *   eviction and re-import, or a fiber restart): claimed/admitted running
 *   automatic work drains and settles under its original receipt.
 */
export type DriverDisposeCause = 'removal' | 'replacement'

/**
 * Collapse the typed CLI failure into one public-safe GoalBar category.
 * The raw message, stderr, and paths stay in the host warning log only.
 */
function admissionFailureCategory(value: unknown): GoalBarAdmissionFailureCategoryV1 {
  if (!(value instanceof LoopXCliError)) return 'driver_error'
  switch (value.kind) {
    case 'missing':
      return 'cli_unavailable'
    case 'typed_failure':
      return 'admission_rejected'
    case 'invalid_schema':
    case 'invalid_json':
      return 'invalid_response'
    default:
      return 'cli_unreachable'
  }
}

/** Exact-session driver with typed activation and no binding mirror or failure counter. */
export class LoopXContinuationDriver {
  private readonly states = new Map<Agent, DriverState>()
  private readonly isLiveAgent: (agent: Agent) => boolean
  private readonly runner: FileRunner | undefined
  private readonly commandResolver: (signal: AbortSignal) => Promise<LoopXCommand>
  private readonly clock: DriverClock
  private readonly makeTurnInstanceId: () => string
  private readonly retryDelaysMs: readonly number[]
  private readonly runDetached: <T>(operation: () => Promise<T>) => Promise<T>
  private readonly warn: (message: string) => void
  private readonly onAdmissionChange: ((session: Agent['session']) => void) | undefined
  private readonly todoUnitFold: TodoUnitFoldPolicy | undefined
  private readonly runObsCommand: TodoUnitObsRunner | undefined
  private command?: LoopXCommand | undefined
  private disposed = false

  constructor(options: LoopXDriverOptions) {
    this.isLiveAgent = options.isLiveAgent
    this.runner = options.runner
    this.commandResolver = options.resolveCommand
      ?? (signal => resolveLoopXCommand({ runner: this.runner, signal }))
    this.clock = options.clock ?? systemClock
    this.makeTurnInstanceId = options.makeTurnInstanceId
      ?? (() => `dsh-loopx-${randomUUID()}`)
    this.retryDelaysMs = options.retryDelaysMs ?? [200, 750]
    this.runDetached = options.runDetached ?? (operation => operation())
    this.warn = message => {
      try { options.warn?.(message) } catch {
        // Diagnostic exporters must not interrupt admission or message restoration.
      }
    }
    this.onAdmissionChange = options.onAdmissionChange
    this.todoUnitFold = options.todoUnitFold
    this.runObsCommand = options.runObsCommand
      ?? (options.todoUnitFold?.enabled === true
        ? createDefaultDshObsRunner(options.runner ?? runFile)
        : undefined)
  }

  /** Record a Todo semantic-unit anchor pinning parent KV prefix Q before exploratory steps. */
  anchorTodoUnit(agent: Agent, input: TodoUnitAnchorInput): TodoUnitAnchor | null {
    if (this.todoUnitFold?.enabled !== true) return null
    if (typeof agent.session.deriveMessages !== 'function') return null
    const anchor = recordDshTodoUnitAnchor(agent.session, input)
    const retained = retainedState(agent, agent.session)
    retained.todoUnitAnchors ??= new Map()
    retained.todoUnitAnchors.set(anchor.todoId, anchor)
    return anchor
  }

  /**
   * Default-off FoldAndPrefetch hook executed when a Todo semantic unit settles:
   * atomically pops contiguous suffix `[anchor..now] -> [mu + Obs(next_cmd)]` on
   * quiescent boundaries while keeping parent prefix Q byte-identical and tool pairing valid.
   */
  onTodoUnitSettled(agent: Agent, request: TodoUnitFoldRequest): Promise<TodoUnitFoldOutcome> {
    const policy = this.todoUnitFold
    if (policy?.enabled !== true) {
      return Promise.resolve(applyTodoUnitFoldAndPrefetch(agent.session, policy, request))
    }
    const state = this.stateFor(agent)
    const retained = retainedState(agent, agent.session)
    const foldKey = request.completionReceipt?.completionReceiptId
      ? `${request.todoId}:${request.turnInstanceId}:${request.completionReceipt.completionReceiptId}:${request.contextEpoch}`
      : undefined
    const cached = foldKey !== undefined ? retained.todoUnitFolds?.get(foldKey) : undefined
    if (cached !== undefined) {
      return Promise.resolve(cached)
    }
    if (foldKey !== undefined) {
      const inFlight = retained.todoUnitFoldsInFlight?.get(foldKey)
      if (inFlight !== undefined) {
        return inFlight
      }
      const inFlightMap = retained.todoUnitFoldsInFlight ??= new Map()
      let execution!: Promise<TodoUnitFoldOutcome>
      execution = (async (): Promise<TodoUnitFoldOutcome> => {
        try {
          return await this.executeTodoUnitSettled(agent, state, retained, policy, request, foldKey)
        } finally {
          if (inFlightMap.get(foldKey) === execution) {
            inFlightMap.delete(foldKey)
          }
        }
      })()
      inFlightMap.set(foldKey, execution)
      return execution
    }
    return this.executeTodoUnitSettled(agent, state, retained, policy, request, undefined)
  }

  private async executeTodoUnitSettled(
    agent: Agent,
    state: DriverState,
    retained: SessionDriverRetention,
    policy: TodoUnitFoldPolicy,
    request: TodoUnitFoldRequest,
    foldKey: string | undefined,
  ): Promise<TodoUnitFoldOutcome> {
    const quiescent = isTodoUnitDriverQuiescent(agent, state)
      && typeof agent.session.header.cwd === 'string'
      && agent.session.header.cwd.length > 0
      && request.quiescentBoundary !== false
    const sessionCwd = agent.session.header.cwd
    let storedAnchor = retained.todoUnitAnchors?.get(request.todoId)
    const activeAnchor = request.anchor ?? storedAnchor
    let effectiveRequest: TodoUnitFoldRequest = {
      ...request,
      quiescentBoundary: quiescent,
    }
    let foldSession = agent.session
    if (
      quiescent
      && typeof sessionCwd === 'string'
      && sessionCwd.length > 0
      && activeAnchor !== undefined
      && activeAnchor.todoId === request.todoId
      && activeAnchor.turnInstanceId === request.turnInstanceId
      && activeAnchor.contextEpoch === request.contextEpoch
      && request.completionReceipt?.exitCode === 0
      && request.completionReceipt.todoId.trim() === request.todoId
      && hasValidDshToolCallPairing(agent.session, activeAnchor.prefixNodeCount)
      && this.runObsCommand !== undefined
    ) {
      const safeArgv = validateTodoUnitObsForExecution(request.obsNextCmd)
      if (safeArgv !== null && request.obsNextCmd !== undefined) {
        const prefetchPin = captureTodoUnitPrefetchPin(agent, state, retained, request.todoId, activeAnchor)
        const obsRes = await this.runObsCommand(safeArgv, sessionCwd)
        const interferenceReason = detectTodoUnitPrefetchInterference(
          this.disposed,
          agent,
          state,
          retained,
          request.todoId,
          request.anchor,
          prefetchPin,
        )
        const obsNextCmd = {
          ...request.obsNextCmd,
          command: safeArgv,
          exitCode: obsRes.exitCode,
          output: obsRes.output,
          timedOut: obsRes.timedOut,
          truncated: obsRes.truncated,
        }
        if (interferenceReason !== null) {
          if (
            interferenceReason === 'user_message_during_prefetch'
            || interferenceReason === 'active_unit_changed_during_prefetch'
            || interferenceReason === 'tail_changed_during_prefetch'
          ) {
            retained.todoUnitAnchors?.delete(request.todoId)
          }
          foldSession = prefetchPin.session
          storedAnchor = prefetchPin.storedAnchor
          effectiveRequest = {
            ...effectiveRequest,
            quiescentBoundary: false,
            nonQuiescentReason: interferenceReason,
            expectedTailSeqs: prefetchPin.tailSeqs,
            obsNextCmd,
          }
        } else {
          effectiveRequest = {
            ...effectiveRequest,
            expectedTailSeqs: prefetchPin.tailSeqs,
            obsNextCmd,
          }
        }
      }
    }
    const outcome = applyTodoUnitFoldAndPrefetch(foldSession, policy, effectiveRequest, storedAnchor)
    if (outcome.operator === 'FoldToReceipt') {
      retained.todoUnitAnchors?.delete(request.todoId)
      if (foldKey !== undefined) {
        retained.todoUnitFolds ??= new Map()
        retained.todoUnitFolds.set(foldKey, outcome)
      }
    }
    return outcome
  }

  /** Named alias matching FoldAndPrefetch hook semantics on the real Driver. */
  foldAndPrefetch(agent: Agent, request: TodoUnitFoldRequest): Promise<TodoUnitFoldOutcome> {
    return this.onTodoUnitSettled(agent, request)
  }

  observeAgent(agent: Agent): void {
    const state = this.stateFor(agent)
    state.activation = foldSessionActivation(agent.session)
    if (agent.status === 'idle') this.requestEvaluation(state)
  }

  onAgentDisposed(agent: Agent): void {
    const state = this.states.get(agent)
    if (state === undefined) return
    this.stopState(state)
    this.states.delete(agent)
  }

  onSessionStart(agent: Agent): void {
    const state = this.stateFor(agent)
    this.cancelPending(state)
    this.retireReservation(state)
    state.competing = inboxHasPending(agent.inbox)
    state.recoveryAttention = undefined
    state.retrySelection = false
    state.schedulerToken = ''
    state.unchangedPolls = 0
    state.commands.clear()
    const retained = retainedState(agent, agent.session)
    retained.todoUnitAnchors?.clear()
    retained.todoUnitFolds?.clear()
    retained.activeTodoUnit = undefined
    retained.todoUnitContextEpoch = undefined
    retained.todoUnitStepCount = 0
    state.activation = foldSessionActivation(agent.session)
    this.requestEvaluation(state)
  }

  onAgentStatus(agent: Agent, status: AgentStatus): void {
    if (status !== 'idle') return
    const state = this.stateFor(agent)
    if (state.reservation?.phase === 'claimed' || state.reservation?.phase === 'admitted') {
      this.retireReservation(state)
    }
    state.competing = inboxHasPending(agent.inbox)
    if (!state.pauseAfterTurnError) this.requestEvaluation(state)
  }

  onInboxInserted(agent: Agent, message: UserMessage): void {
    const state = this.stateFor(agent)
    if (state.reservation !== undefined && sameReservation(message, state.reservation)) return
    state.competing = true
    if (message.source.kind === 'user') this.resumeAfterError(state)
    this.cancelPending(state)
  }

  onInboxClaimed(agent: Agent, message: UserMessage): void {
    const state = this.stateFor(agent)
    if (state.reservation !== undefined && sameReservation(message, state.reservation)) {
      state.reservation.phase = 'claimed'
      return
    }
    state.competing = true
  }

  onInboxDiscarded(agent: Agent, message: UserMessage): void {
    const state = this.states.get(agent)
    if (state?.reservation !== undefined && sameReservation(message, state.reservation)) {
      this.retireReservation(state)
    }
  }

  onAgentError(agent: Agent): void {
    const state = this.stateFor(agent)
    this.setErrorPause(state, true)
    this.cancelPending(state)
  }

  hasRecoveryAttention(capture: GoalBarSessionCapture, expected: GoalBarExpectedBindingV1): boolean {
    if (!this.captureIsLive(capture)) return false
    const state = this.states.get(capture.agent)
    return state?.activation.session === capture.session
      && state.recoveryAttention?.goalId === expected.goalId
      && state.recoveryAttention.agentId === expected.loopxAgentId
  }

  async evaluateActivatedSession(
    capture: GoalBarSessionCapture,
  ): Promise<GoalBarDriverActionReceipt> {
    if (!this.captureIsLive(capture)) {
      return { kind: 'unavailable', reason: 'session_unavailable' }
    }
    const state = this.stateFor(capture.agent)
    if (state.stopping || state.activation.session !== capture.session) {
      return { kind: 'unavailable', reason: 'session_unavailable' }
    }

    retainedState(capture.agent, capture.session).goalBarPaused = false
    this.resumeAfterError(state)
    state.activation = foldSessionActivation(capture.session)
    if (!state.activation.activated) return { kind: 'applied' }

    state.retrySelection = state.activation.pendingSelection !== undefined
    const evaluated = new Promise<boolean>(resolve => {
      state.evaluationWaiters.add({ session: capture.session, resolve })
    })
    this.requestEvaluation(state)

    if (!(await evaluated)) {
      return this.captureIsLive(capture)
        ? { kind: 'unavailable', reason: 'evaluation_unavailable' }
        : { kind: 'unavailable', reason: 'session_unavailable' }
    }
    return this.captureIsLive(capture)
      && state.activation.session === capture.session
      ? { kind: 'applied' }
      : { kind: 'unavailable', reason: 'session_unavailable' }
  }

  async cancelQueued(
    capture: GoalBarSessionCapture,
  ): Promise<GoalBarDriverActionReceipt> {
    if (!this.captureIsLive(capture)) {
      return { kind: 'unavailable', reason: 'session_unavailable' }
    }
    const state = this.stateFor(capture.agent)
    if (state.stopping || state.activation.session !== capture.session) {
      return { kind: 'unavailable', reason: 'session_unavailable' }
    }

    const retained = retainedState(capture.agent, capture.session)
    retained.goalBarPaused = true
    const reservation = state.reservation
    if (reservation?.phase === 'claimed' || reservation?.phase === 'admitted') {
      reservation.preserveAcrossPause = true
    }
    state.retrySelection = false
    this.cancelPending(state)
    // Pause takes over continuation authority; a latched admission failure
    // would otherwise keep warning on a Goal the user already paused.
    this.clearAdmissionFailure(state)
    return this.captureIsLive(capture)
      && state.activation.session === capture.session
      ? { kind: 'applied' }
      : { kind: 'unavailable', reason: 'session_unavailable' }
  }

  readContinuationAdmission(
    capture: GoalBarSessionCapture,
  ): GoalBarContinuationAdmissionV1 {
    const state = this.states.get(capture.agent)
    if (state?.activation.session !== capture.session || capture.agent.session !== capture.session) {
      return GOALBAR_ADMISSION_CLEAR
    }
    const category = retainedState(capture.agent, capture.session).admissionFailure
    return category === undefined
      ? GOALBAR_ADMISSION_CLEAR
      : { state: 'failed', category }
  }

  onSessionEvent(agent: Agent, event: SessionEvent): void {
    const existing = this.states.get(agent)
    if (existing === undefined) return
    const state = this.stateFor(agent)
    const wasActivated = state.activation.activated
    foldActivationEvent(state.activation, event)
    foldSelectionEvent(state.activation, event)
    if (!wasActivated && state.activation.activated
      && agent.status === 'idle' && !state.pauseAfterTurnError) {
      this.requestEvaluation(state)
    }
    if (event.type === 'command/run') {
      state.commands.add(String(event.data.commandId))
      state.competing = true
      this.cancelPending(state)
      return
    }
    if (event.type === 'command/done') {
      state.commands.delete(String(event.data.commandId))
      if (state.commands.size === 0) {
        state.competing = inboxHasPending(agent.inbox)
        if (agent.status === 'idle' && !state.pauseAfterTurnError) {
          this.requestEvaluation(state)
        }
      }
      return
    }
    if (event.type === 'user/message'
      && state.reservation !== undefined
      && event.data.id === state.reservation.messageId) {
      state.reservation.phase = 'admitted'
      const retained = retainedState(agent, agent.session)
      if (retained.activeTodoUnit?.turnInstanceId === state.reservation.turnInstanceId) {
        retained.activeTodoUnit.admittedSeq = event.seq
      }
      return
    }
    if (event.type === 'user/message' && event.data.source?.kind === 'user') {
      retainedState(agent, agent.session).activeTodoUnit = undefined
    }
    if (event.type === 'step/start') {
      const retained = retainedState(agent, agent.session)
      retained.todoUnitStepCount = (retained.todoUnitStepCount ?? 0) + 1
    }
    if (event.type === 'turn/end') {
      const retained = retainedState(agent, agent.session)
      const active = retained.activeTodoUnit
      if (active !== undefined && active.completedCleanly === undefined) {
        active.endStepIndex = Math.max(active.startStepIndex, retained.todoUnitStepCount ?? active.startStepIndex)
        active.completedCleanly = event.data.reason.kind === 'completed'
      }
      if (event.data.reason.kind === 'max-tokens' || event.data.reason.kind === 'aborted') {
        this.setErrorPause(state, true)
        this.cancelPending(state)
      }
    }
  }

  async onPreStep(
    agent: Agent,
    messages: UserMessage[],
    signal: AbortSignal,
    next: () => Promise<PreStepDecision>,
  ): Promise<PreStepDecision> {
    const state = this.stateFor(agent)
    const reservation = state.reservation
    const submitted = reservation === undefined
      ? messages.find(message => isDriverMessage(message))
      : messages.find(message => message.id === reservation.messageId)
        ?? messages.find(message => isDriverMessage(message))
    if (submitted === undefined) return next()
    const startedAt = performance.now()
    if (reservation === undefined || !sameReservation(submitted, reservation)
      || reservation.phase !== 'claimed') {
      this.warn(`dsh-loopx-driver: ${preStepRejection(reservation === undefined ? 'missing-reservation' : !sameReservation(submitted, reservation) ? 'reservation-mismatch' : 'reservation-not-claimed', messages, submitted, startedAt)}`)
      if (reservation !== undefined && submitted.id === reservation.messageId) {
        this.retireReservation(state)
      }
      this.restoreCompetingMessages(agent, otherStepInputs(messages, submitted))
      return { kind: 'reject' }
    }
    if (messages.some(message => message.id !== submitted.id && !isHostContextMessage(message))) {
      this.warn(`dsh-loopx-driver: ${preStepRejection('competing-messages', messages, submitted, startedAt)}`)
      this.retireReservation(state)
      this.restoreCompetingMessages(agent, otherStepInputs(messages, submitted))
      return { kind: 'reject' }
    }

    if (!(await this.authorityStillAllows(state, reservation, signal))) {
      this.warn(`dsh-loopx-driver: ${preStepRejection('authority-before-next', messages, submitted, startedAt)}`)
      this.retireReservation(state)
      this.restoreCompetingMessages(agent, otherStepInputs(messages, submitted))
      this.requestEvaluation(state)
      return { kind: 'reject' }
    }

    let decision: PreStepDecision
    try {
      decision = await next()
    } catch (error: unknown) {
      this.setErrorPause(state, true)
      this.retireReservation(state)
      throw error
    }
    if (signal.aborted) {
      this.setErrorPause(state, true)
      this.retireReservation(state)
      return decision
    }
    if (decision.kind === 'reject') {
      this.warn(`dsh-loopx-driver: ${preStepRejection('downstream-reject', messages, submitted, startedAt)}`)
      this.setErrorPause(state, true)
      this.retireReservation(state)
      return decision
    }
    const admitted = decision.messages.find(message => message.id === reservation.messageId)
    if (decision.messages.filter(message => !isHostContextMessage(message)).length !== 1
      || admitted === undefined
      || !sameReservation(admitted, reservation)) {
      this.warn(`dsh-loopx-driver: ${preStepRejection('downstream-batch', decision.messages, submitted, startedAt)}`)
      this.retireReservation(state)
      const remaining = otherStepInputs(decision.messages, submitted)
      const acceptedInputs = remaining.filter(message => !inboxContains(agent.inbox, message.id))
      if (acceptedInputs.some(message => message.source.kind === 'user')) {
        // Keep the downstream-approved human step. Putting the messages back
        // after a rejection would leave them parked until another Host wake.
        return { ...decision, messages: acceptedInputs }
      }
      const restoredWork = this.restoreCompetingMessages(agent, remaining)
      if (!restoredWork && !inboxHasIndependentInput(agent.inbox)) {
        this.setErrorPause(state, true)
      }
      return { kind: 'reject' }
    }
    if (!(await this.authorityStillAllows(state, reservation, signal))) {
      this.warn(`dsh-loopx-driver: ${preStepRejection('authority-after-next', decision.messages, submitted, startedAt)}`)
      this.retireReservation(state)
      this.restoreCompetingMessages(agent, otherStepInputs(decision.messages, submitted))
      return { kind: 'reject' }
    }
    return decision
  }

  /**
   * Tear the Driver down under a typed unload cause.
   *
   * `removal` (default) keeps the historical semantics: an already claimed or
   * admitted automatic Turn that is still running is cancelled with
   * `{ kind: 'parent' }` and the dispose awaits idle. `replacement` covers
   * plugin-tree recomposition (module eviction and re-import, fiber restart):
   * future scheduling still retires (timers, evaluations, queued-but-unclaimed
   * reservations), but a running admitted Turn keeps its Session-owned
   * execution and its single settlement identity and simply drains — the
   * successor Driver re-attaches to the live Agent, admits nothing while it
   * runs, and evaluates freshly once the Turn settles and the Agent is idle.
   */
  async dispose(cause: DriverDisposeCause = 'removal'): Promise<void> {
    if (this.disposed) return
    this.disposed = true
    const waits: Promise<void>[] = []
    for (const state of this.states.values()) {
      const activeAutomaticStep = state.reservation?.phase === 'claimed'
        || state.reservation?.phase === 'admitted'
      this.stopState(state)
      if (activeAutomaticStep && state.agent.status === 'running') {
        if (cause === 'removal') {
          try {
            state.agent.cancel({ kind: 'parent' })
            waits.push(state.agent.whenIdle())
          } catch (error: unknown) {
            this.warn(`dsh-loopx-driver: could not cancel active work: ${renderThrown(error)}`)
          }
        }
      }
      if (state.run !== undefined) waits.push(state.run)
    }
    await Promise.allSettled(waits)
    this.states.clear()
  }

  private stateFor(agent: Agent): DriverState {
    const existing = this.states.get(agent)
    if (existing !== undefined) {
      if (existing.activation.session !== agent.session) {
        this.resolveEvaluationWaiters(existing, false)
        this.cancelPending(existing)
        this.retireReservation(existing)
        existing.competing = inboxHasPending(agent.inbox)
        existing.recoveryAttention = undefined
        existing.retrySelection = false
        existing.schedulerToken = ''
        existing.unchangedPolls = 0
        existing.commands.clear()
        existing.activation = foldSessionActivation(agent.session)
      }
      return existing
    }
    const state: DriverState = {
      agent,
      activation: foldSessionActivation(agent.session),
      requested: false,
      stopping: false,
      competing: inboxHasPending(agent.inbox),
      get pauseAfterTurnError() { return retainedState(agent, this.activation.session).paused },
      get reservation() { return retainedState(agent, this.activation.session).reservation },
      set reservation(value: Reservation | undefined) { retainedState(agent, this.activation.session).reservation = value },
      retrySelection: false,
      schedulerToken: '',
      unchangedPolls: 0,
      commands: new Set(),
      evaluationWaiters: new Set(),
    }
    this.states.set(agent, state)
    return state
  }

  private captureIsLive(capture: GoalBarSessionCapture): boolean {
    return !this.disposed
      && capture.agent.session === capture.session
      && this.isLiveAgent(capture.agent)
      && capture.agent.id === capture.session.id
      && capture.agent.id === capture.session.header.id
  }

  private ready(state: DriverState): boolean {
    return !this.disposed
      && !state.stopping
      && state.activation.session === state.agent.session
      && state.activation.activated
      && !retainedState(state.agent, state.activation.session).goalBarPaused
      && !state.pauseAfterTurnError
      && !state.competing
      && state.commands.size === 0
      && state.reservation === undefined
      && this.isLiveAgent(state.agent)
      && state.agent.status === 'idle'
      && !inboxHasPending(state.agent.inbox)
      && state.agent.id === state.agent.session.id
      && state.agent.id === state.agent.session.header.id
      && typeof state.agent.session.header.cwd === 'string'
      && state.agent.session.header.cwd.length > 0
  }

  private requestEvaluation(state: DriverState): void {
    if (state.stopping || this.disposed
      || state.activation.session !== state.agent.session
      || !state.activation.activated) return
    state.requested = true
    if (state.run !== undefined) return
    let run: Promise<void>
    try {
      run = this.runDetached(async () => {
        while (state.requested && !state.stopping) {
          state.requested = false
          const waiters = [...state.evaluationWaiters]
          for (const waiter of waiters) state.evaluationWaiters.delete(waiter)
          let evaluated = false
          try {
            evaluated = await this.evaluate(state)
          } finally {
            this.resolveEvaluationWaiterBatch(state, waiters, evaluated)
          }
        }
      })
    } catch (error: unknown) {
      state.requested = false
      this.resolveEvaluationWaiters(state, false)
      this.warn(`dsh-loopx-driver: could not start evaluation: ${renderThrown(error)}`)
      return
    }
    state.run = run
    const retire = (): void => {
      state.run = undefined
      if (state.requested && !state.stopping) this.requestEvaluation(state)
    }
    void run.then(retire, error => {
      this.warn(`dsh-loopx-driver: evaluation failed: ${renderThrown(error)}`)
      retire()
    })
  }

  private async evaluate(state: DriverState): Promise<boolean> {
    if (!this.ready(state)) return false
    const session = state.agent.session
    const startedAt = performance.now()
    let stage: AdmissionStage = 'maintenance-slot'
    let admissionSignal: AbortSignal | undefined
    const controller = new AbortController()
    state.controller = controller
    let evaluationStarted = false
    let waitingForIdle = false
    let plan: EvaluationPlan
    try {
      plan = await state.agent.runMaintenance(async maintenanceSignal => {
        evaluationStarted = true
        const signal = AbortSignal.any([controller.signal, maintenanceSignal])
        admissionSignal = signal
        return this.buildPlan(state, signal, value => { stage = value })
      })
    } catch (error: unknown) {
      if (!evaluationStarted && !controller.signal.aborted
        && !(error instanceof LoopXCliError)) {
        // Maintenance keeps public status idle; wait for the actual slot to be free.
        waitingForIdle = true
        void state.agent.whenIdle().then(() => {
          if (state.controller === controller && !controller.signal.aborted) {
            this.requestEvaluation(state)
          }
        }).catch((idleError: unknown) => {
          if (state.controller === controller && !controller.signal.aborted) {
            this.warn(`dsh-loopx-driver: could not wait for idle: ${renderThrown(idleError)}`)
          }
        }).finally(() => {
          if (state.controller === controller) state.controller = undefined
        })
        return false
      }
      const cancelled = error instanceof LoopXCliError && error.kind === 'aborted'
      if (error instanceof LoopXCliError && error.kind === 'missing') {
        this.command = undefined
      }
      if (!controller.signal.aborted && !cancelled) {
        this.warn(`dsh-loopx-driver: admission_failed stage=${stage} ${cliErrorFields(error)} signal_aborted=${admissionSignal?.aborted ?? controller.signal.aborted} elapsed_ms=${Math.round(performance.now() - startedAt)}`)
        this.failAdmission(state, error)
      }
      return evaluationStarted
    } finally {
      if (!waitingForIdle && state.controller === controller) state.controller = undefined
    }

    // Cancellation can land after the maintenance callback has built its plan.
    if (admissionSignal?.aborted || state.agent.session !== session) return false
    if (this.ready(state)) {
      state.recoveryAttention = plan.kind === 'wait' ? plan.recoveryAttention : undefined
    }

    if (plan.kind === 'wait') {
      if (plan.schedulerToken !== undefined) state.schedulerToken = plan.schedulerToken
      if (plan.unchangedPolls !== undefined) state.unchangedPolls = plan.unchangedPolls
      if (plan.delayMs !== undefined) this.schedule(state, plan.delayMs)
      this.clearAdmissionFailure(state)
      return true
    }
    state.schedulerToken = ''
    state.unchangedPolls = 0
    this.clearAdmissionFailure(state)
    if (!this.ready(state) || state.agent.session !== plan.session) return true
    const message = automaticMessage(plan)
    if (this.todoUnitFold?.enabled === true) {
      const retained = retainedState(state.agent, plan.session)
      const contextEpoch = retained.todoUnitContextEpoch
        ??= `dsh:${plan.session.header.id}:${randomUUID()}`
      const startStepIndex = retained.todoUnitStepCount ?? 0
      const anchor = plan.todoId !== undefined
        ? this.anchorTodoUnit(state.agent, {
          todoId: plan.todoId,
          turnInstanceId: plan.turnInstanceId,
          contextEpoch,
          anchorStepIndex: startStepIndex,
        })
        : null
      retained.activeTodoUnit = {
        goalId: plan.goalId,
        agentId: plan.agentId,
        todoId: plan.todoId,
        turnInstanceId: plan.turnInstanceId,
        contextEpoch,
        startStepIndex,
        endStepIndex: startStepIndex,
        anchor: anchor ?? undefined,
      }
    }
    const reservation: Reservation = {
      messageId: message.id,
      content: message.content,
      source: message.source,
      session: plan.session,
      goalId: plan.goalId,
      agentId: plan.agentId,
      turnInstanceId: plan.turnInstanceId,
      phase: 'queued',
      preserveAcrossPause: false,
    }
    state.reservation = reservation
    try {
      state.agent.followup(message)
    } catch (error: unknown) {
      if (this.todoUnitFold?.enabled === true) {
        const retained = retainedState(state.agent, plan.session)
        if (retained.activeTodoUnit?.turnInstanceId === plan.turnInstanceId) {
          if (retained.activeTodoUnit.todoId !== undefined) {
            retained.todoUnitAnchors?.delete(retained.activeTodoUnit.todoId)
          }
          retained.activeTodoUnit = undefined
        }
      }
      this.retireReservation(state)
      this.warn(`dsh-loopx-driver: could not queue same-session work: ${renderThrown(error)}`)
    }
    return true
  }

  private ensureReady(
    state: DriverState,
    signal: AbortSignal,
    session: Agent['session'],
  ): void {
    if (signal.aborted || !this.ready(state) || state.agent.session !== session) {
      throw new LoopXCliError('aborted', 'automatic admission was fenced', false)
    }
  }

  private async buildPlan(
    state: DriverState,
    signal: AbortSignal,
    setStage: (stage: AdmissionStage) => void,
  ): Promise<EvaluationPlan> {
    const session = state.agent.session
    this.ensureReady(state, signal, session)
    setStage('resolve-command')
    const command = await this.loopXCommand(signal)
    this.ensureReady(state, signal, session)
    setStage('resolve-binding')
    const bindingPayload = await this.resolveBinding(command, state.agent, signal)
    this.ensureReady(state, signal, session)
    if (bindingPayload.ok !== true) {
      throw new LoopXCliError(
        'typed_failure',
        'LoopX binding authority rejected automatic continuation',
        false,
      )
    }
    const binding = exactBinding(bindingPayload, String(state.agent.id))
    if (binding === undefined) {
      state.recoveryAttention = undefined
      return { kind: 'wait', schedulerToken: '', unchangedPolls: 0 }
    }

    setStage('quota')
    const selection = state.activation.pendingSelection
    let turnInstanceId: string | undefined
    if (selection !== undefined
      && selection.goalId === binding.goalId && selection.agentId === binding.agentId) {
      const receipt = await this.readSelectionReceipt(command, state.agent, selection, signal)
      this.ensureReady(state, signal, session)
      if (receipt.selection_required === true && receipt.settlement_binding_owed === true) {
        if (!state.retrySelection && selection.completedAttempts >= SELECTION_RECOVERY_COMPLETION_LIMIT) {
          return { kind: 'wait', recoveryAttention: binding }
        }
        turnInstanceId = selection.turnInstanceId
      } else {
        state.activation.pendingSelection = undefined
        state.recoveryAttention = undefined
      }
    } else {
      state.activation.pendingSelection = undefined
    }
    state.retrySelection = false
    turnInstanceId ??= this.makeTurnInstanceId()
    const quota = await this.readQuota(
      command,
      state.agent,
      binding,
      turnInstanceId,
      { signal, includeSelectedTodo: this.todoUnitFold?.enabled === true },
    )
    this.ensureReady(state, signal, session)
    if (quota.ok !== true) {
      throw new LoopXCliError(
        'typed_failure',
        'LoopX quota authority rejected automatic continuation',
        receiptWriteFailed(quota, turnInstanceId),
      )
    }
    if (typeof quota.should_run !== 'boolean' || !exactQuotaContext(quota, binding)
      || (quota.should_run === true && !exactQuotaReceipt(quota, turnInstanceId))) {
      throw new LoopXCliError(
        'invalid_schema',
        'LoopX quota response did not match the bound Goal and Agent',
        false,
      )
    }
    if (this.todoUnitFold?.enabled === true) {
      await this.tryFoldSettledActiveTodoUnit(command, state, binding, quota, signal)
      this.ensureReady(state, signal, session)
    }
    if (quota.should_run !== true) {
      const wait = schedulerWait(quota, state.schedulerToken, state.unchangedPolls)
      const recovery = record(quota.unsettled_host_turn_recovery)
      const attention = quota.should_run === false
        && recovery?.schema_version === 'unsettled_host_turn_recovery_v0'
        && recovery.state === 'attention_required'
      state.recoveryAttention = attention ? binding : undefined
      return attention ? { ...wait, recoveryAttention: binding } : wait
    }
    // A verified new Core decision clears the old hold even if reading its
    // task body later fails. A body transport error cannot revive a prior wait.
    state.recoveryAttention = undefined
    setStage('heartbeat')
    const heartbeat = await this.readHeartbeat(
      command,
      state.agent,
      binding,
      turnInstanceId,
      signal,
    )
    this.ensureReady(state, signal, session)
    if (heartbeat.ok !== true) {
      throw new LoopXCliError(
        'typed_failure',
        'LoopX heartbeat authority rejected automatic continuation',
        false,
      )
    }
    const taskBody = exactHeartbeat(heartbeat, binding, turnInstanceId)
    if (taskBody === undefined) {
      throw new LoopXCliError(
        'invalid_schema',
        'LoopX heartbeat response did not match the bound Goal and Agent',
        false,
      )
    }
    const receipt = record(quota.heartbeat_receipt)
    const todoId = this.todoUnitFold?.enabled === true
      ? extractTodoIdFromQuotaAndHeartbeat(quota, heartbeat)
      : undefined
    return {
      kind: 'queue',
      ...binding,
      session,
      turnInstanceId,
      taskBody: taskBodyWithRewardMemory(taskBody, quota),
      selectionRequired: (receipt?.status === 'committed' || receipt?.status === 'replayed')
        && receipt.selection_required === true && receipt.settlement_binding_owed === true,
      ...(todoId !== undefined ? { todoId } : {}),
    }
  }

  private async authorityStillAllows(
    state: DriverState,
    reservation: Reservation,
    signal: AbortSignal,
  ): Promise<boolean> {
    if (!this.reservationIsCurrent(state, reservation, signal)) return false
    const startedAt = performance.now()
    try {
      const command = await this.loopXCommand(signal)
      const bindingPayload = await this.resolveBinding(command, state.agent, signal)
      if (bindingPayload.ok !== true) {
        throw new LoopXCliError('typed_failure', 'LoopX binding authority rejected automatic continuation', false)
      }
      const binding = exactBinding(bindingPayload, String(state.agent.id))
      if (binding?.goalId !== reservation.goalId || binding.agentId !== reservation.agentId) {
        return false
      }
      if (reservation.preserveAcrossPause) {
        return this.reservationIsCurrent(state, reservation, signal)
      }
      const quota = await this.readQuota(
        command,
        state.agent,
        reservation,
        reservation.turnInstanceId,
        { signal, revalidateOnly: true },
      )
      if (!this.reservationIsCurrent(state, reservation, signal)) return false
      if (reservation.preserveAcrossPause) return true
      if (quota.ok !== true) {
        throw new LoopXCliError('typed_failure', 'LoopX quota authority rejected automatic continuation',
          receiptWriteFailed(quota, reservation.turnInstanceId))
      }
      return exactQuota(quota, reservation, reservation.turnInstanceId)
    } catch (error: unknown) {
      if (error instanceof LoopXCliError && error.kind === 'missing') {
        this.command = undefined
      }
      if (!(error instanceof LoopXCliError && error.kind === 'aborted')) {
        this.warn(`dsh-loopx-driver: admission_failed stage=pre-step-recheck ${cliErrorFields(error)} elapsed_ms=${Math.round(performance.now() - startedAt)}`)
        if (!reservation.preserveAcrossPause && this.reservationIsCurrent(state, reservation, signal)) {
          this.failAdmission(state, error)
        }
      }
      return false
    }
  }

  private reservationIsCurrent(
    state: DriverState,
    reservation: Reservation,
    signal: AbortSignal,
  ): boolean {
    return !signal.aborted
      && !this.disposed
      && !state.stopping
      && !state.pauseAfterTurnError
      && !state.competing
      && state.activation.activated
      && state.activation.session === state.agent.session
      && state.agent.session === reservation.session
      && state.reservation === reservation
      && this.isLiveAgent(state.agent)
      && state.agent.id === reservation.session.id
      && state.agent.id === reservation.session.header.id
  }

  private async loopXCommand(signal: AbortSignal): Promise<LoopXCommand> {
    if (this.command !== undefined) return this.command
    const resolved = await this.commandResolver(signal)
    if (!signal.aborted) {
      this.command = resolved
      if (coreRouteBin(resolved) === undefined) {
        // Without a bound executable, Core renders the bare `loopx` fallback.
        this.warn(
          `dsh-loopx-driver: cli_route_unpinned candidate=${resolved.candidate ?? 'unknown'}`
          + ' hint=point LOOPX_BIN at one absolute loopx executable to pin continuation commands',
        )
      }
    }
    return resolved
  }

  private resolveBinding(
    command: LoopXCommand,
    agent: Agent,
    signal: AbortSignal,
  ): Promise<Record<string, unknown>> {
    return runJsonCommand(
      command,
      [
        '--registry', '.loopx/registry.json',
        '--format', 'json',
        'resolve-agent-thread',
        '--host-surface', HOST_SURFACE,
        '--thread-id', String(agent.id),
      ],
      {
        runner: this.runner,
        cwd: agent.session.header.cwd,
        signal,
        attempts: 3,
        retryDelaysMs: this.retryDelaysMs,
        validate: payload => payload.schema_version === RESOLUTION_SCHEMA
          && payload.host_surface === HOST_SURFACE
          && payload.thread_id === String(agent.id),
      },
    )
  }

  private async readQuota(
    command: LoopXCommand,
    agent: Agent,
    binding: Binding,
    turnInstanceId: string,
    {
      signal,
      revalidateOnly = false,
      includeSelectedTodo = false,
    }: { signal: AbortSignal; revalidateOnly?: boolean; includeSelectedTodo?: boolean },
  ): Promise<Record<string, unknown>> {
    // The 2.3 CLI supplies immutable navigation and its bounded reader. Older
    // supported CLIs continue to answer this Host-only call with raw JSON.
    const progressive = supportedLoopXVersion(command.version, '2.3.0') !== undefined
    const options = {
      runner: this.runner,
      cwd: agent.session.header.cwd,
      signal,
      attempts: 3,
      retryDelaysMs: this.retryDelaysMs,
    }
    const rawAdmission = (payload: Record<string, unknown>) => payload.goal_id === binding.goalId
      && typeof payload.ok === 'boolean' && typeof payload.should_run === 'boolean'
    const quota = await runJsonCommand(
      command,
      [
        '--registry', '.loopx/registry.json',
        '--format', 'json',
        ...coreRouteArgs(command),
        'quota', 'should-run',
        '--goal-id', binding.goalId,
        '--agent-id', binding.agentId,
        '--runtime-profile', 'generic_cli',
        ...(progressive && revalidateOnly ? [] : ['--include-detail', 'scheduler']),
        '--turn-instance-id', turnInstanceId,
      ],
      {
        ...options,
        ...(progressive ? {} : { env: { ...process.env, LOOPX_CONTEXT_DELIVERY: 'off' } }),
        validate: payload => rawAdmission(payload) || (progressive && quotaNavigationReference(payload) !== undefined),
      },
    )
    const reference = progressive ? quotaNavigationReference(quota) : undefined
    if (reference === undefined) return quota
    // Retry the exact saved page on transport loss, outside the guard retry.
    const decision = await readQuotaCapture(
      command, reference, options, revalidateOnly, includeSelectedTodo,
    )
    if (!rawAdmission(decision)) {
      throw new LoopXCliError('invalid_schema', 'LoopX quota capture did not match the bound Goal', false)
    }
    return decision
  }

  private async tryFoldSettledActiveTodoUnit(
    command: LoopXCommand,
    state: DriverState,
    binding: Binding,
    quota: Record<string, unknown>,
    signal: AbortSignal,
  ): Promise<void> {
    const policy = this.todoUnitFold
    if (policy?.enabled !== true) return
    const retained = retainedState(state.agent, state.activation.session)
    const active = retained.activeTodoUnit
    if (
      active === undefined
      || active.goalId !== binding.goalId
      || active.agentId !== binding.agentId
      || active.completedCleanly !== true
      || state.activation.pendingSelection !== undefined
      || state.pauseAfterTurnError
    ) {
      return
    }
    const recovery = record(quota.unsettled_host_turn_recovery)
    if (
      recovery?.schema_version === 'unsettled_host_turn_recovery_v0'
      && recovery.state === 'attention_required'
    ) {
      retained.activeTodoUnit = undefined
      return
    }
    const receiptAnchor = active.anchor
      ?? (active.todoId !== undefined ? retained.todoUnitAnchors?.get(active.todoId) : undefined)
    const receiptPin = captureTodoUnitPrefetchPin(
      state.agent,
      state,
      retained,
      active.todoId ?? '',
      receiptAnchor,
    )
    try {
      const readback = await runJsonCommand(command, [
        '--registry', '.loopx/registry.json', '--format', 'json', 'quota', 'receipt',
        '--include-settlement',
        '--goal-id', binding.goalId, '--agent-id', binding.agentId,
        '--turn-instance-id', active.turnInstanceId,
      ], {
        runner: this.runner,
        cwd: state.agent.session.header.cwd,
        signal,
      })
      this.ensureReady(state, signal, state.activation.session)
      if (
        detectTodoUnitPrefetchInterference(
          this.disposed,
          state.agent,
          state,
          retained,
          active.todoId ?? '',
          active.anchor,
          receiptPin,
        ) !== null
      ) {
        return
      }
      const decoded = decodeQuotaSettlementReadback(
        readback,
        binding.goalId,
        binding.agentId,
        active.turnInstanceId,
        active.todoId,
      )
      if (!decoded.verified) {
        retained.activeTodoUnit = undefined
        return
      }
      const receipt = decoded.completionReceipt
      if (active.anchor === undefined && active.admittedSeq !== undefined) {
        this.anchorTodoUnit(state.agent, {
          todoId: receipt.todoId,
          turnInstanceId: active.turnInstanceId,
          contextEpoch: active.contextEpoch,
          anchorStepIndex: active.startStepIndex,
          anchorSurfaceSeq: active.admittedSeq,
        })
      }
      const nextTodoId = extractTodoIdFromQuotaAndHeartbeat(quota)
      const obsNextCmd = policy.resolveObsNextCmd?.({
        goalId: binding.goalId,
        agentId: binding.agentId,
        settledTodoId: receipt.todoId,
        nextTodoId,
        quota,
      }) ?? policy.obsNextCmd
      await this.onTodoUnitSettled(state.agent, {
        todoId: receipt.todoId,
        turnInstanceId: active.turnInstanceId,
        contextEpoch: active.contextEpoch,
        nowStepIndex: Math.max(active.startStepIndex, active.endStepIndex),
        completionReceipt: receipt,
        obsNextCmd,
        failureFacts: [...receipt.failureFacts, ...(policy.defaultFailureFacts ?? [])],
        openObligations: [...receipt.openObligations, ...(policy.defaultOpenObligations ?? [])],
        quiescentBoundary: true,
      })
    } catch (error: unknown) {
      if (error instanceof LoopXCliError && error.kind === 'aborted') throw error
      this.warn(`dsh-loopx-driver: todo_unit_fold_kept_raw error=${renderThrown(error)}`)
    } finally {
      if (retained.activeTodoUnit === active) {
        retained.activeTodoUnit = undefined
      }
    }
  }

  private async readSelectionReceipt(
    command: LoopXCommand,
    agent: Agent,
    selection: SelectionTurn,
    signal: AbortSignal,
  ): Promise<Record<string, unknown>> {
    const readback = await runJsonCommand(command, [
      '--registry', '.loopx/registry.json', '--format', 'json', 'quota', 'receipt',
      '--goal-id', selection.goalId, '--agent-id', selection.agentId,
      '--turn-instance-id', selection.turnInstanceId,
    ], { runner: this.runner, cwd: agent.session.header.cwd, signal })
    const receipt = record(readback.heartbeat_receipt)
    const identity = record(receipt?.settlement_identity)
    if (readback.schema_version !== RECEIPT_READBACK_SCHEMA
      || readback.ok !== true || readback.goal_id !== selection.goalId
      || readback.agent_id !== selection.agentId || readback.turn_instance_id !== selection.turnInstanceId
      || receipt?.status !== 'committed' || receipt.turn_instance_id !== selection.turnInstanceId
      || !(receipt.selection_required === true && receipt.settlement_binding_owed === true
        || identity?.goal_id === selection.goalId && identity.agent_id === selection.agentId
          && identity.turn_instance_id === selection.turnInstanceId)) {
      throw new LoopXCliError('invalid_schema', 'LoopX could not verify the original Turn selection', false)
    }
    return receipt
  }

  private readHeartbeat(
    command: LoopXCommand,
    agent: Agent,
    binding: Binding,
    turnInstanceId: string,
    signal: AbortSignal,
  ): Promise<Record<string, unknown>> {
    return runJsonCommand(
      command,
      [
        '--registry', '.loopx/registry.json',
        '--format', 'json',
        // The global option binds Core's command-route context for this
        // invocation; the heartbeat-prompt option names the executable its
        // rendered task-body commands embed.
        ...coreRouteArgs(command),
        'heartbeat-prompt', '--thin',
        ...coreRouteArgs(command),
        '--goal-id', binding.goalId,
        '--agent-id', binding.agentId,
        '--runtime-profile', 'generic_cli',
        '--turn-instance-id', turnInstanceId,
      ],
      {
        runner: this.runner,
        cwd: agent.session.header.cwd,
        signal,
        attempts: 3,
        retryDelaysMs: this.retryDelaysMs,
        validate: payload => payload.schema_version === HEARTBEAT_SCHEMA
          && payload.goal_id === binding.goalId
          && payload.agent_id === binding.agentId
          && payload.turn_instance_id === turnInstanceId,
      },
    )
  }

  private schedule(state: DriverState, delayMs: number): void {
    if (state.stopping || this.disposed
      || state.activation.session !== state.agent.session
      || !state.activation.activated) return
    if (state.timer !== undefined) this.clock.clearTimeout(state.timer)
    const handle = this.clock.setTimeout(() => {
      if (state.timer !== handle) return
      state.timer = undefined
      this.requestEvaluation(state)
    }, Math.max(0, Math.min(delayMs, MAX_WAIT_MS)))
    state.timer = handle
  }

  private resolveEvaluationWaiterBatch(
    state: DriverState,
    waiters: readonly EvaluationWaiter[],
    evaluated: boolean,
  ): void {
    for (const waiter of waiters) {
      waiter.resolve(evaluated
        && !this.disposed
        && !state.stopping
        && state.activation.session === waiter.session
        && state.agent.session === waiter.session
        && this.isLiveAgent(state.agent))
    }
  }

  private resolveEvaluationWaiters(
    state: DriverState,
    evaluated: boolean,
  ): void {
    const waiters = [...state.evaluationWaiters]
    state.evaluationWaiters.clear()
    this.resolveEvaluationWaiterBatch(state, waiters, evaluated)
  }

  private cancelPending(state: DriverState): void {
    state.requested = false
    state.controller?.abort()
    state.controller = undefined
    if (state.timer !== undefined) {
      this.clock.clearTimeout(state.timer)
      state.timer = undefined
    }
    const reservation = state.reservation
    if (reservation === undefined || reservation.phase !== 'queued') return
    state.agent.inbox.remove(reservation.messageId)
    this.retireReservation(state)
  }

  private retireReservation(state: DriverState): void {
    const reservation = state.reservation
    if (reservation === undefined) return
    if (reservation.phase !== 'admitted') {
      const retained = retainedState(state.agent, state.activation.session)
      if (retained.activeTodoUnit?.turnInstanceId === reservation.turnInstanceId) {
        if (retained.activeTodoUnit.todoId !== undefined) {
          retained.todoUnitAnchors?.delete(retained.activeTodoUnit.todoId)
        }
        retained.activeTodoUnit = undefined
      }
    }
    reservation.phase = 'retired'
    state.reservation = undefined
  }

  private failAdmission(state: DriverState, error: unknown): void {
    const category = admissionFailureCategory(error)
    // A recoverable receipt write failure keeps the next-idle retry; genuine rejections wait for a human.
    const recoverable = error instanceof LoopXCliError && error.retryable
    if (category === 'admission_rejected' && !recoverable) this.setErrorPause(state, true)
    const retained = retainedState(state.agent, state.activation.session)
    if (retained.admissionFailure === category) return
    retained.admissionFailure = category
    this.onAdmissionChange?.(state.activation.session)
  }

  private setErrorPause(state: DriverState, paused: boolean): void {
    retainedState(state.agent, state.activation.session).paused = paused
  }

  private resumeAfterError(state: DriverState): void {
    this.setErrorPause(state, false)
    if (retainedState(state.agent, state.activation.session).admissionFailure === 'admission_rejected') {
      this.clearAdmissionFailure(state)
    }
  }

  private clearAdmissionFailure(state: DriverState): void {
    const retained = retainedState(state.agent, state.activation.session)
    if (retained.admissionFailure === undefined) return
    retained.admissionFailure = undefined
    this.onAdmissionChange?.(state.activation.session)
  }

  private restoreCompetingMessages(
    agent: Agent,
    messages: readonly UserMessage[],
  ): boolean {
    // Host context belongs to the step that requested it. Restore it only
    // alongside independent input that will actually use it.
    if (messages.every(isHostContextMessage)) return false
    for (const message of messages) {
      if (inboxContains(agent.inbox, message.id)) continue
      agent.send(message, 'next-step', true)
    }
    return true
  }

  private stopState(state: DriverState): void {
    state.stopping = true
    this.resolveEvaluationWaiters(state, false)
    this.cancelPending(state)
  }
}

/**
 * Watch the host's plugin lifecycle so the Driver's unload can distinguish
 * plugin-tree recomposition (a replacement Driver is loaded into the same live
 * host) from user-initiated disposal. Cordis exposes no typed unload reason,
 * so this reads the two mechanics that separate them in the supported hosts:
 *
 * - A restart-driven unload (`Fiber.update`/`Fiber.restart`, e.g. a config or
 *   dependency refresh) keeps the fiber uid; the same fiber re-runs `apply`
 *   after the unload completes.
 * - A module replacement (dsh-hmr evicts the changed module and its
 *   importers) deletes the plugin runtime from the registry *before*
 *   disposing its fibers. Cordis then emits `internal/plugin` for the cleared
 *   uid while the runtime is already absent. User disable/uninstall, entry
 *   removal, and host cascade disposal instead call `fiber.dispose()`
 *   directly, so the runtime is still registered at that same instant (the
 *   fiber disposer removes it immediately afterwards).
 *
 * Anything unobservable stays `removal`, preserving the pre-existing
 * cancellation semantics for unknown or older hosts.
 */
function watchReplacementUnload(ctx: Context): () => boolean {
  const fiber: Fiber | undefined = ctx.fiber
  const callback = fiber?.runtime?.callback
  let moduleReplacement = false
  ctx.on('internal/plugin', (disposed: Fiber) => {
    if (disposed !== fiber || fiber.uid !== null || callback === undefined) return
    moduleReplacement = !ctx.registry.has(callback)
  })
  return () => moduleReplacement || (fiber !== undefined && fiber.uid !== null)
}

export function apply(
  ctx: Context,
  config?: { readonly todoUnitFold?: TodoUnitFoldPolicy | undefined },
): void {
  const driver = new LoopXContinuationDriver({
    isLiveAgent: agent => ctx.agents.get(agent.id) === agent,
    resolveCommand: signal => resolvePluginLoopXCommand({ signal }),
    runDetached: operation => ctx.agents.withoutInitiator(operation),
    warn: message => { ctx.logger.warn(message) },
    onAdmissionChange: session => { goalBarCoordinator.publishAdmissionChange(session) },
    ...(config?.todoUnitFold !== undefined ? { todoUnitFold: config.todoUnitFold } : {}),
  })
  const replacementUnload = watchReplacementUnload(ctx)

  ctx.effect(function* () {
    const unregisterDriverBridge = goalBarCoordinator.registerDriverBridge(driver)
    // DSH 0.2 announces startup/resume/clear/compact through agent/created.
    // Reset the exact Session's state at that boundary, before input is driven.
    ctx.on('agent/created', ({ agent }) => { driver.onSessionStart(agent) })
    ctx.on('agent/disposed', ({ agent }) => {
      goalBarCoordinator.invalidateSession(agent.session)
      driver.onAgentDisposed(agent)
    })
    ctx.on('session/disposed', session => {
      goalBarCoordinator.invalidateSession(session)
    })
    // Retain the real 0.1 lifecycle contract for previously supported hosts.
    // The event was removed from 0.2's declarations and is not emitted there.
    const legacyLifecycle = ctx as Context & {
      on(event: 'agent/session-start', listener: (payload: { agent: Agent }) => void): unknown
    }
    legacyLifecycle.on('agent/session-start', ({ agent }) => { driver.onSessionStart(agent) })
    ctx.on('agent/status', ({ agent, status }) => {
      if (status === 'idle' || status === 'running') {
        goalBarCoordinator.publishAgentStatus(agent.session, status)
      }
      driver.onAgentStatus(agent, status)
    })
    ctx.on('agent/inbox/inserted', ({ agent, message }) => driver.onInboxInserted(agent, message))
    ctx.on('agent/inbox/claimed', ({ agent, message }) => driver.onInboxClaimed(agent, message))
    ctx.on('agent/inbox/discarded', ({ agent, message }) => driver.onInboxDiscarded(agent, message))
    ctx.on('agent/error', ({ agent }) => { driver.onAgentError(agent) })
    ctx.on('agent/pre-step', ({ agent, messages, signal }, next) => (
      driver.onPreStep(agent, messages, signal, next)
    ))
    ctx.on('session/event', (session, event) => {
      if (event.type === 'step/end' || event.type === 'turn/end') {
        goalBarCoordinator.publishSessionCandidate(session, event)
      }
      const agent = ctx.agents.get(session.id)
      if (agent?.session === session) driver.onSessionEvent(agent, event)
    })
    for (const agent of ctx.agents.list()) driver.observeAgent(agent)
    yield async () => {
      unregisterDriverBridge()
      await driver.dispose(replacementUnload() ? 'replacement' : 'removal')
    }
  }, 'dsh-loopx-driver lifecycle')
}
