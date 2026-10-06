import { randomUUID } from 'node:crypto'
import { isDeepStrictEqual } from 'node:util'
import type { Context } from '@deepseek-ai/cordis'
import type { Agent, AgentStatus, PreStepDecision } from '@deepseek-ai/dsh-agent'
import type {} from '@deepseek-ai/dsh-commands'
import type {} from '@deepseek-ai/dsh-llm'
import type {} from '@deepseek-ai/dsh-session'
import type { SessionEvent, UserMessage } from '@deepseek-ai/dsh-session'
import {
  LoopXCliError,
  resolveLoopXCommand,
  runJsonCommand,
} from './cli.ts'
import type { FileRunner, LoopXCommand } from './cli.ts'
import { resolvePluginLoopXCommand } from './managed-runtime.ts'
import { goalBarCoordinator } from './goalbar/events.ts'
import type {
  GoalBarDriverActionReceipt,
  GoalBarSessionCapture,
} from './goalbar/events.ts'

export const name = 'dsh-loopx-driver'
export const inject = ['agents', 'loopxBootstrap']

const HOST_SURFACE = 'deepseek-harness-native'
const RESOLUTION_SCHEMA = 'loopx_thread_agent_binding_resolution_v0'
const HEARTBEAT_SCHEMA = 'heartbeat_agent_input_v1'
const CONTINUATION_SCHEMA = 'loopx_dsh_continuation_v0'
const DEFAULT_WAIT_MS = 5 * 60_000
const MAX_WAIT_MS = 24 * 60 * 60_000

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

interface LoopXContinuationMessageSource {
  readonly kind: 'loopx-continuation'
  readonly schemaVersion: typeof CONTINUATION_SCHEMA
  readonly goalId: string
  readonly agentId: string
  readonly turnInstanceId: string
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

interface ActivationProjection {
  readonly session: Agent['session']
  readonly pendingModelCalls: Set<string>
  activated: boolean
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
  pauseAfterTurnError: boolean
  schedulerToken: string
  unchangedPolls: number
  readonly commands: Set<string>
  readonly evaluationWaiters: Set<EvaluationWaiter>
  run?: Promise<void> | undefined
  controller?: AbortController | undefined
  timer?: TimerHandle | undefined
  reservation?: Reservation | undefined
}

interface QueuePlan extends Binding {
  readonly kind: 'queue'
  readonly session: Agent['session']
  readonly turnInstanceId: string
  readonly taskBody: string
}

interface WaitPlan {
  readonly kind: 'wait'
  readonly delayMs?: number | undefined
  readonly schedulerToken?: string | undefined
  readonly unchangedPolls?: number | undefined
}

type EvaluationPlan = QueuePlan | WaitPlan

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
  const source = exactRecord(message.source, [
    'kind',
    'schemaVersion',
    'goalId',
    'agentId',
    'turnInstanceId',
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
  return continuationSource(message) !== undefined
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
  const successful = source?.kind === 'tool'
    && message?.role === 'user'
    && block?.type === 'tool-result'
    && block.toolCallId === callId
    && (block.isError === undefined || block.isError === false)
    && data?.error === undefined
  if (matched && successful) {
    projection.activated = true
    projection.pendingModelCalls.clear()
  }
}

function foldSessionActivation(session: Agent['session']): ActivationProjection {
  const projection: ActivationProjection = {
    session,
    pendingModelCalls: new Set(),
    activated: false,
  }
  try {
    for (const event of session.snapshotEvents()) foldActivationEvent(projection, event)
  } catch {
    projection.pendingModelCalls.clear()
    projection.activated = false
  }
  return projection
}

/**
 * Whether an Agent still holds input it has not consumed. The 0.1.5 Inbox
 * exposes the two pending queues instead of the retired `hasPending` flag.
 */
function inboxHasPending(inbox: Agent['inbox']): boolean {
  return inbox.nextTurn.length > 0 || inbox.nextStep.length > 0
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

function schedulerWait(
  payload: Record<string, unknown>,
  currentToken: string,
  currentPolls: number,
): WaitPlan {
  const scheduler = record(payload.scheduler_hint)
  const unchanged = record(scheduler?.unchanged_poll)
  const local = record(unchanged?.local_scheduler)
  if (local === undefined) return { kind: 'wait' }

  const reset = record(scheduler?.reset_policy)
  const token = typeof reset?.reset_token === 'string' ? reset.reset_token : ''
  const unchangedPolls = token.length > 0 && token === currentToken
    ? currentPolls
    : 0
  const rawLimit = local.unchanged_poll_limit
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
  return {
    kind: 'wait',
    delayMs: Math.min(Math.round(minutes * 60_000), MAX_WAIT_MS),
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
  const assignments = [...taskBody.matchAll(/\bLOOPX_TURN=([^\s`'";]+)/gu)]
    .map(match => match[1])
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
    this.warn = options.warn ?? (() => {})
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
    state.pauseAfterTurnError = false
    state.schedulerToken = ''
    state.unchangedPolls = 0
    state.commands.clear()
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
    if (message.source.kind === 'user') state.pauseAfterTurnError = false
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
    state.pauseAfterTurnError = true
    this.cancelPending(state)
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

    state.activation = foldSessionActivation(capture.session)
    if (!state.activation.activated) return { kind: 'applied' }

    state.pauseAfterTurnError = false
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
    const state = this.states.get(capture.agent)
    if (state === undefined) return { kind: 'applied' }
    if (state.stopping || state.activation.session !== capture.session) {
      return { kind: 'unavailable', reason: 'session_unavailable' }
    }

    const reservation = state.reservation
    if (reservation?.phase === 'claimed' || reservation?.phase === 'admitted') {
      reservation.preserveAcrossPause = true
    }
    this.cancelPending(state)
    return this.captureIsLive(capture)
      && state.activation.session === capture.session
      ? { kind: 'applied' }
      : { kind: 'unavailable', reason: 'session_unavailable' }
  }

  onSessionEvent(agent: Agent, event: SessionEvent): void {
    const existing = this.states.get(agent)
    if (existing === undefined) return
    const state = this.stateFor(agent)
    const wasActivated = state.activation.activated
    foldActivationEvent(state.activation, event)
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
      return
    }
    if (event.type === 'turn/end'
      && (event.data.reason.kind === 'max-tokens' || event.data.reason.kind === 'aborted')) {
      state.pauseAfterTurnError = true
      this.cancelPending(state)
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
    if (reservation === undefined || !sameReservation(submitted, reservation)
      || reservation.phase !== 'claimed') {
      if (reservation !== undefined && submitted.id === reservation.messageId) {
        this.retireReservation(state)
      }
      this.restoreOtherMessages(agent, messages, submitted)
      return { kind: 'reject' }
    }
    if (messages.some(message => message.id !== submitted.id)) {
      this.retireReservation(state)
      this.restoreOtherMessages(agent, messages, submitted)
      return { kind: 'reject' }
    }

    if (!(await this.authorityStillAllows(state, reservation, signal))) {
      this.retireReservation(state)
      this.restoreOtherMessages(agent, messages, submitted)
      this.requestEvaluation(state)
      return { kind: 'reject' }
    }

    let decision: PreStepDecision
    try {
      decision = await next()
    } catch (error: unknown) {
      state.pauseAfterTurnError = true
      this.retireReservation(state)
      throw error
    }
    if (signal.aborted) {
      state.pauseAfterTurnError = true
      this.retireReservation(state)
      return decision
    }
    if (decision.kind === 'reject') {
      state.pauseAfterTurnError = true
      this.retireReservation(state)
      return decision
    }
    const admitted = decision.messages.find(message => message.id === reservation.messageId)
    if (decision.messages.length !== 1
      || admitted === undefined
      || !sameReservation(admitted, reservation)) {
      this.retireReservation(state)
      this.restoreOtherMessages(agent, decision.messages, submitted)
      return { kind: 'reject' }
    }
    if (!(await this.authorityStillAllows(state, reservation, signal))) {
      this.retireReservation(state)
      this.restoreOtherMessages(agent, decision.messages, submitted)
      return { kind: 'reject' }
    }
    return decision
  }

  async dispose(): Promise<void> {
    if (this.disposed) return
    this.disposed = true
    const waits: Promise<void>[] = []
    for (const state of this.states.values()) {
      const activeAutomaticStep = state.reservation?.phase === 'claimed'
        || state.reservation?.phase === 'admitted'
      this.stopState(state)
      if (activeAutomaticStep && state.agent.status === 'running') {
        try {
          state.agent.cancel({ kind: 'parent' })
          waits.push(state.agent.whenIdle())
        } catch (error: unknown) {
          this.warn(`dsh-loopx-driver: could not cancel active work: ${renderThrown(error)}`)
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
        existing.pauseAfterTurnError = false
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
      pauseAfterTurnError: false,
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
    const controller = new AbortController()
    state.controller = controller
    let evaluationStarted = false
    let plan: EvaluationPlan
    try {
      plan = await state.agent.runMaintenance(async maintenanceSignal => {
        evaluationStarted = true
        const signal = AbortSignal.any([controller.signal, maintenanceSignal])
        return this.buildPlan(state, signal)
      })
    } catch (error: unknown) {
      const cancelled = error instanceof LoopXCliError && error.kind === 'aborted'
      if (error instanceof LoopXCliError && error.kind === 'missing') {
        this.command = undefined
      }
      if (!controller.signal.aborted && !cancelled) {
        this.warn(`dsh-loopx-driver: LoopX admission failed: ${renderThrown(error)}`)
      }
      return evaluationStarted
    } finally {
      if (state.controller === controller) state.controller = undefined
    }

    if (plan.kind === 'wait') {
      if (plan.schedulerToken !== undefined) state.schedulerToken = plan.schedulerToken
      if (plan.unchangedPolls !== undefined) state.unchangedPolls = plan.unchangedPolls
      if (plan.delayMs !== undefined) this.schedule(state, plan.delayMs)
      return true
    }
    state.schedulerToken = ''
    state.unchangedPolls = 0
    if (!this.ready(state) || state.agent.session !== plan.session) return true
    const message = automaticMessage(plan)
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
  ): Promise<EvaluationPlan> {
    const session = state.agent.session
    this.ensureReady(state, signal, session)
    const command = await this.loopXCommand(signal)
    this.ensureReady(state, signal, session)
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
      return { kind: 'wait', schedulerToken: '', unchangedPolls: 0 }
    }

    const turnInstanceId = this.makeTurnInstanceId()
    const quota = await this.readQuota(command, state.agent, binding, turnInstanceId, signal)
    this.ensureReady(state, signal, session)
    if (quota.ok !== true) {
      throw new LoopXCliError(
        'typed_failure',
        'LoopX quota authority rejected automatic continuation',
        false,
      )
    }
    if (!exactQuotaContext(quota, binding)
      || (quota.should_run === true && !exactQuotaReceipt(quota, turnInstanceId))) {
      throw new LoopXCliError(
        'invalid_schema',
        'LoopX quota response did not match the bound Goal and Agent',
        false,
      )
    }
    if (quota.should_run !== true) {
      return schedulerWait(quota, state.schedulerToken, state.unchangedPolls)
    }
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
    return {
      kind: 'queue',
      ...binding,
      session,
      turnInstanceId,
      taskBody: taskBodyWithRewardMemory(taskBody, quota),
    }
  }

  private async authorityStillAllows(
    state: DriverState,
    reservation: Reservation,
    signal: AbortSignal,
  ): Promise<boolean> {
    if (!this.reservationIsCurrent(state, reservation, signal)) return false
    try {
      const command = await this.loopXCommand(signal)
      const bindingPayload = await this.resolveBinding(command, state.agent, signal)
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
        signal,
      )
      return this.reservationIsCurrent(state, reservation, signal)
        && (reservation.preserveAcrossPause
          || exactQuota(quota, reservation, reservation.turnInstanceId))
    } catch (error: unknown) {
      if (error instanceof LoopXCliError && error.kind === 'missing') {
        this.command = undefined
      }
      if (!(error instanceof LoopXCliError && error.kind === 'aborted')) {
        this.warn(`dsh-loopx-driver: pre-step authority check failed: ${renderThrown(error)}`)
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
    if (!signal.aborted) this.command = resolved
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

  private readQuota(
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
        'quota', 'should-run',
        '--goal-id', binding.goalId,
        '--agent-id', binding.agentId,
        '--runtime-profile', 'generic_cli',
        '--include-detail', 'scheduler',
        '--turn-instance-id', turnInstanceId,
      ],
      {
        runner: this.runner,
        cwd: agent.session.header.cwd,
        signal,
        attempts: 3,
        retryDelaysMs: this.retryDelaysMs,
        validate: payload => payload.goal_id === binding.goalId
          && typeof payload.ok === 'boolean'
          && typeof payload.should_run === 'boolean',
      },
    )
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
        'heartbeat-prompt', '--thin',
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
    if (state.reservation === undefined) return
    state.reservation.phase = 'retired'
    state.reservation = undefined
  }

  private restoreOtherMessages(
    agent: Agent,
    messages: UserMessage[],
    submitted: UserMessage,
  ): void {
    for (const message of messages) {
      if (message.id === submitted.id || isDriverMessage(message)) continue
      if (agent.inbox.nextStep.some(candidate => candidate.id === message.id)
        || agent.inbox.nextTurn.some(candidate => candidate.id === message.id)) continue
      agent.send(message, 'next-step', true)
    }
  }

  private stopState(state: DriverState): void {
    state.stopping = true
    this.resolveEvaluationWaiters(state, false)
    this.cancelPending(state)
  }
}

export function apply(ctx: Context): void {
  const driver = new LoopXContinuationDriver({
    isLiveAgent: agent => ctx.agents.get(agent.id) === agent,
    resolveCommand: signal => resolvePluginLoopXCommand({ signal }),
    runDetached: operation => ctx.agents.withoutInitiator(operation),
    warn: message => { ctx.logger.warn(message) },
  })

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
      await driver.dispose()
    }
  }, 'dsh-loopx-driver lifecycle')
}
