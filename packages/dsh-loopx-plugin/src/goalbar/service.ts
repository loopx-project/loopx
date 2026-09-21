import type { Agent } from '@deepseek-ai/dsh-agent'
import {
  LoopXCliError,
  resolveLoopXCommand,
  runJsonMutationCommand,
} from '../cli.ts'
import type {
  FileRunner,
  LoopXCommand,
} from '../cli.ts'
import {
  latestSessionEventSeq,
} from './events.ts'
import type {
  GoalBarDriverActionReceipt,
  GoalBarSessionCapture,
  GoalBarWatchService,
} from './events.ts'
import {
  GOALBAR_PROJECT_REGISTRY,
  computeGoalBarSourceRevision,
  readGoalBarActivation,
  readGoalBarBinding,
  readStableGoalBarModel,
  unavailableGoalBarSourceRevision,
} from './read-model.ts'
import {
  GOALBAR_RESPONSE_VERSION,
} from './protocol.ts'
import type {
  GoalBarActionResultV1,
  GoalBarReadFaultCode,
  GoalBarReadResultV1,
  GoalBarRequestV1,
  GoalBarResponseV1,
  GoalBarWatchResultV1,
} from './protocol.ts'

const ACTIVATION_TRANSITION_SCHEMA = 'loopx_goal_activation_transition_v1'
const ACTIVATION_READBACK_SCHEMA = 'loopx_goal_activation_readback_v1'
const AMBIGUOUS_LOG_CODE = 'dsh_loopx_goalbar_binding_ambiguous'
const DEFAULT_WATCH_TIMEOUT_MS = 25_000
const MAX_WATCH_TIMEOUT_MS = 30_000
const DEFAULT_ACTION_TIMEOUT_MS = 20_000
const MAX_ACTION_TIMEOUT_MS = 60_000
const DEFAULT_ACTION_MAX_OUTPUT_BYTES = 1024 * 1024
const MAX_ACTION_MAX_OUTPUT_BYTES = 2 * 1024 * 1024

type ActionOp = 'start' | 'pause'

interface LiveCapture extends GoalBarSessionCapture {
  readonly sessionId: string
  readonly cwd: string
}

interface Deferred {
  readonly promise: Promise<void>
  resolve(): void
}

interface ActionAdmission {
  readonly mutationSettled: Promise<void>
  readonly completion: Promise<void>
  resolveMutation(): void
  resolveCompletion(): void
}

export interface GoalBarCoordinatorPort {
  openWatchService(): GoalBarWatchService
  evaluateActivatedSession(
    capture: GoalBarSessionCapture,
  ): Promise<GoalBarDriverActionReceipt>
  cancelQueued(
    capture: GoalBarSessionCapture,
  ): Promise<GoalBarDriverActionReceipt>
}

export interface GoalBarServiceOptions {
  readonly getAgent: (sessionId: string) => Agent | undefined
  readonly coordinator: GoalBarCoordinatorPort
  readonly command?: LoopXCommand | undefined
  readonly resolveCommand?: (
    signal: AbortSignal,
  ) => Promise<LoopXCommand>
  readonly runner?: FileRunner | undefined
  readonly env?: NodeJS.ProcessEnv | undefined
  readonly retryDelaysMs?: readonly number[] | undefined
  readonly watchTimeoutMs?: number | undefined
  readonly actionTimeoutMs?: number | undefined
  readonly actionMaxOutputBytes?: number | undefined
  readonly warn?: ((message: string) => void) | undefined
}

export interface GoalBarServiceHandle {
  handle(
    request: GoalBarRequestV1,
    signal: AbortSignal,
  ): Promise<GoalBarResponseV1>
  dispose(): Promise<void>
}

function deferred(): Deferred {
  let settled = false
  let settle!: () => void
  const promise = new Promise<void>(resolve => { settle = resolve })
  return {
    promise,
    resolve() {
      if (settled) return
      settled = true
      settle()
    },
  }
}

function boundedInteger(
  value: number | undefined,
  fallback: number,
  maximum: number,
): number {
  return Number.isSafeInteger(value) && (value ?? -1) >= 0
    ? Math.min(value as number, maximum)
    : fallback
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined
}

function exactRecord(
  value: unknown,
  keys: readonly string[],
): Record<string, unknown> | undefined {
  const input = record(value)
  if (input === undefined) return undefined
  const actual = Reflect.ownKeys(input)
  const allowed = new Set(keys)
  return actual.length === keys.length
    && actual.every(key => typeof key === 'string' && allowed.has(key))
    ? input
    : undefined
}

/** Strict verified relation for the existing lifecycle execution V1 receipt. */
export function decodeGoalBarLifecycleExecutionV1(
  value: unknown,
  goalId: string,
  targetState: 'active' | 'stopped',
): boolean {
  const input = record(value)
  const readback = exactRecord(input?.readback, [
    'schema_version',
    'status',
    'verified',
    'goal_id',
    'expected_state',
    'source_state',
    'target_state',
  ])
  return input?.schema_version === ACTIVATION_TRANSITION_SCHEMA
    && input.goal_id === goalId
    && (input.before_state === 'active' || input.before_state === 'stopped')
    && input.after_state === targetState
    && input.ok === true
    && input.dry_run === false
    && input.execute === true
    && typeof input.changed === 'boolean'
    && typeof input.written === 'boolean'
    && input.partial_write === false
    && readback?.schema_version === ACTIVATION_READBACK_SCHEMA
    && readback.status === 'verified'
    && readback.verified === true
    && readback.goal_id === goalId
    && readback.expected_state === targetState
    && readback.source_state === targetState
    && readback.target_state === targetState
}

export function fixedGoalBarFailureResponseV1(
  request: GoalBarRequestV1,
): GoalBarResponseV1 {
  if (request.op === 'read') {
    return {
      v: GOALBAR_RESPONSE_VERSION,
      op: 'read',
      sessionId: request.sessionId,
      result: {
        kind: 'fault',
        code: 'protocol_mismatch',
        baseSessionEventSeq: null,
        sourceRevision: unavailableGoalBarSourceRevision(),
      },
    }
  }
  if (request.op === 'watch') {
    return {
      v: GOALBAR_RESPONSE_VERSION,
      op: 'watch',
      sessionId: request.sessionId,
      result: { kind: 'fault', code: 'session_unavailable' },
    }
  }
  return {
    v: GOALBAR_RESPONSE_VERSION,
    op: request.op,
    sessionId: request.sessionId,
    result: { kind: 'unknown', code: 'operation_result_unknown' },
  }
}

export class GoalBarService implements GoalBarServiceHandle {
  private readonly getAgent: (sessionId: string) => Agent | undefined
  private readonly coordinator: GoalBarCoordinatorPort
  private readonly watchService: GoalBarWatchService
  private readonly runner: FileRunner | undefined
  private readonly env: NodeJS.ProcessEnv | undefined
  private readonly retryDelaysMs: readonly number[] | undefined
  private readonly commandResolver: (signal: AbortSignal) => Promise<LoopXCommand>
  private readonly watchTimeoutMs: number
  private readonly actionTimeoutMs: number
  private readonly actionMaxOutputBytes: number
  private readonly warn: (message: string) => void
  private readonly disposal = new AbortController()
  private activeAction?: ActionAdmission | undefined
  private disposed = false

  constructor(options: GoalBarServiceOptions) {
    this.getAgent = options.getAgent
    this.coordinator = options.coordinator
    this.watchService = options.coordinator.openWatchService()
    this.runner = options.runner
    this.env = options.env
    this.retryDelaysMs = options.retryDelaysMs
    this.watchTimeoutMs = boundedInteger(
      options.watchTimeoutMs,
      DEFAULT_WATCH_TIMEOUT_MS,
      MAX_WATCH_TIMEOUT_MS,
    )
    this.actionTimeoutMs = boundedInteger(
      options.actionTimeoutMs,
      DEFAULT_ACTION_TIMEOUT_MS,
      MAX_ACTION_TIMEOUT_MS,
    )
    this.actionMaxOutputBytes = boundedInteger(
      options.actionMaxOutputBytes,
      DEFAULT_ACTION_MAX_OUTPUT_BYTES,
      MAX_ACTION_MAX_OUTPUT_BYTES,
    )
    this.warn = options.warn ?? (() => {})
    this.commandResolver = options.resolveCommand
      ?? (options.command === undefined
        ? signal => resolveLoopXCommand({
            runner: this.runner,
            signal,
            env: this.env,
          })
        : async () => options.command as LoopXCommand)
  }

  async handle(
    request: GoalBarRequestV1,
    signal: AbortSignal,
  ): Promise<GoalBarResponseV1> {
    try {
      if (request.op === 'read') return await this.readResponse(request, signal)
      if (request.op === 'watch') return await this.watchResponse(request, signal)
      return await this.actionResponse(request, signal)
    } catch {
      return fixedGoalBarFailureResponseV1(request)
    }
  }

  async dispose(): Promise<void> {
    if (!this.disposed) {
      this.disposed = true
      this.disposal.abort()
      this.watchService.dispose()
    }
    await this.activeAction?.completion
  }

  private capture(sessionId: string): LiveCapture | undefined {
    if (this.disposed) return undefined
    let agent: Agent | undefined
    try {
      agent = this.getAgent(sessionId)
    } catch {
      return undefined
    }
    if (agent === undefined) return undefined
    const session = agent.session
    const cwd = session.header.cwd
    return String(agent.id) === sessionId
      && String(session.id) === sessionId
      && agent.session === session
      && (agent.status === 'idle' || agent.status === 'running')
      && typeof cwd === 'string'
      && cwd.length > 0
      ? { agent, session, sessionId, cwd }
      : undefined
  }

  private captureIsCurrent(capture: LiveCapture): boolean {
    try {
      return this.getAgent(capture.sessionId) === capture.agent
        && capture.agent.session === capture.session
        && String(capture.agent.id) === capture.sessionId
        && String(capture.session.id) === capture.sessionId
    } catch {
      return false
    }
  }

  private combinedSignal(signal: AbortSignal): AbortSignal {
    return AbortSignal.any([signal, this.disposal.signal])
  }

  private logAmbiguity(sessionId: string, count: number): void {
    const boundedSessionId = [...sessionId].slice(0, 128).join('')
    const boundedCount = Math.min(Math.max(count, 0), 9999)
    try {
      this.warn(
        `${AMBIGUOUS_LOG_CODE} session_id=${boundedSessionId} count=${boundedCount}`,
      )
    } catch {
      // Logging must never change the authority result.
    }
  }

  private async waitForAdmittedMutation(signal: AbortSignal): Promise<boolean> {
    const admitted = this.activeAction
    if (admitted === undefined) return !signal.aborted
    if (signal.aborted) return false
    return await new Promise(resolve => {
      let settled = false
      const finish = (value: boolean): void => {
        if (settled) return
        settled = true
        signal.removeEventListener('abort', aborted)
        resolve(value)
      }
      const aborted = (): void => { finish(false) }
      signal.addEventListener('abort', aborted, { once: true })
      admitted.mutationSettled.then(
        () => { finish(!signal.aborted) },
        () => { finish(false) },
      )
    })
  }

  private async readResult(
    sessionId: string,
    signal: AbortSignal,
  ): Promise<GoalBarReadResultV1> {
    const capture = this.capture(sessionId)
    if (capture === undefined) {
      return {
        kind: 'fault',
        code: 'session_unavailable',
        baseSessionEventSeq: null,
        sourceRevision: unavailableGoalBarSourceRevision(),
      }
    }

    // This cursor is captured before command resolution or any other CLI read.
    const baseSessionEventSeq = latestSessionEventSeq(capture.session)
    const operationSignal = this.combinedSignal(signal)
    if (!(await this.waitForAdmittedMutation(operationSignal))
      || !this.captureIsCurrent(capture)) {
      return this.readFault(capture, 'session_unavailable', baseSessionEventSeq)
    }

    let command: LoopXCommand
    try {
      command = await this.commandResolver(operationSignal)
    } catch (error: unknown) {
      const code: GoalBarReadFaultCode = error instanceof LoopXCliError
        && error.kind === 'missing'
        ? 'cli_unavailable'
        : operationSignal.aborted
          ? 'session_unavailable'
          : 'binding_read_failed'
      return this.readFault(capture, code, baseSessionEventSeq)
    }
    if (operationSignal.aborted || !this.captureIsCurrent(capture)) {
      return this.readFault(capture, 'session_unavailable', baseSessionEventSeq)
    }

    let stableRead: Awaited<ReturnType<typeof readStableGoalBarModel>>
    try {
      stableRead = await readStableGoalBarModel({
        command,
        cwd: capture.cwd,
        runner: this.runner,
        signal: operationSignal,
        env: this.env,
        retryDelaysMs: this.retryDelaysMs,
        sessionId: String(capture.agent.id),
        agentStatus: capture.agent.status,
      })
    } catch {
      return {
        kind: 'fault',
        code: 'binding_read_failed',
        baseSessionEventSeq,
        sourceRevision: unavailableGoalBarSourceRevision(),
      }
    }
    const { model, sourceRevision } = stableRead
    if (operationSignal.aborted || !this.captureIsCurrent(capture)) {
      return this.readFault(capture, 'session_unavailable', baseSessionEventSeq)
    }
    if (model.kind === 'hidden') {
      if (model.reason === 'binding_ambiguous') {
        this.logAmbiguity(sessionId, model.uniquePairCount)
      }
      return { kind: 'hidden', reason: model.reason, baseSessionEventSeq, sourceRevision }
    }
    if (model.kind === 'fault') {
      return { kind: 'fault', code: model.code, baseSessionEventSeq, sourceRevision }
    }
    return {
      kind: 'present',
      baseSessionEventSeq,
      sourceRevision,
      snapshot: {
        ...model.snapshot,
        // Status is process-local authority and is refreshed at publication.
        agentStatus: capture.agent.status,
      },
    }
  }

  private async readFault(
    capture: LiveCapture,
    code: GoalBarReadFaultCode,
    baseSessionEventSeq: number | null,
  ): Promise<GoalBarReadResultV1> {
    try {
      return {
        kind: 'fault',
        code,
        baseSessionEventSeq,
        sourceRevision: await computeGoalBarSourceRevision({ cwd: capture.cwd }),
      }
    } catch {
      return {
        kind: 'fault',
        code,
        baseSessionEventSeq,
        sourceRevision: unavailableGoalBarSourceRevision(),
      }
    }
  }

  private async readResponse(
    request: Extract<GoalBarRequestV1, { readonly op: 'read' }>,
    signal: AbortSignal,
  ): Promise<Extract<GoalBarResponseV1, { readonly op: 'read' }>> {
    return {
      v: GOALBAR_RESPONSE_VERSION,
      op: 'read',
      sessionId: request.sessionId,
      result: await this.readResult(request.sessionId, signal),
    }
  }

  private async watchResponse(
    request: Extract<GoalBarRequestV1, { readonly op: 'watch' }>,
    signal: AbortSignal,
  ): Promise<Extract<GoalBarResponseV1, { readonly op: 'watch' }>> {
    const capture = this.capture(request.sessionId)
    let result: GoalBarWatchResultV1
    if (capture === undefined) {
      result = { kind: 'fault', code: 'session_unavailable' }
    } else {
      result = await this.waitForWatchChange(capture, request, signal)
    }
    return {
      v: GOALBAR_RESPONSE_VERSION,
      op: 'watch',
      sessionId: request.sessionId,
      result,
    }
  }

  private async waitForWatchChange(
    capture: LiveCapture,
    request: Extract<GoalBarRequestV1, { readonly op: 'watch' }>,
    signal: AbortSignal,
  ): Promise<GoalBarWatchResultV1> {
    const operationSignal = this.combinedSignal(signal)
    const deadline = Date.now() + this.watchTimeoutMs
    let cursor = request.afterSessionEventSeq
    const upperBound = latestSessionEventSeq(capture.session)
    if (cursor !== null && (upperBound === null || cursor > upperBound)) {
      return { kind: 'fault', code: 'session_unavailable' }
    }
    while (!operationSignal.aborted && this.captureIsCurrent(capture)) {
      const remaining = Math.max(0, deadline - Date.now())
      const receipt = await this.watchService.waitForSessionChange(
        capture.session,
        cursor,
        {
          signal: operationSignal,
          timeoutMs: remaining,
          observedAgentStatus: request.agentStatus,
          isSessionCurrent: () => this.captureIsCurrent(capture),
          getAgentStatus: () => capture.agent.status,
        },
      )
      if (receipt.kind === 'runtime_changed'
        && receipt.sessionId === request.sessionId) {
        try {
          const revision = await computeGoalBarSourceRevision({
            cwd: capture.cwd,
            goalId: request.expected?.goalId,
            loopxAgentId: request.expected?.loopxAgentId,
          })
          if (!this.captureIsCurrent(capture)) {
            return { kind: 'fault', code: 'session_unavailable' }
          }
          if (revision !== request.sourceRevision) {
            return {
              kind: 'source_changed',
              sessionEventSeq: latestSessionEventSeq(capture.session) ?? cursor,
            }
          }
        } catch {
          return { kind: 'fault', code: 'session_unavailable' }
        }
        return {
          kind: 'runtime_changed',
          sessionEventSeq: latestSessionEventSeq(capture.session) ?? cursor,
          agentStatus: receipt.agentStatus,
        }
      }
      if (receipt.kind === 'candidate' && receipt.sessionId === request.sessionId) {
        cursor = receipt.sessionEventSeq
      } else if (receipt.kind === 'timeout') {
        cursor = receipt.sessionEventSeq
      } else {
        return { kind: 'fault', code: 'session_unavailable' }
      }
      try {
        const revision = await computeGoalBarSourceRevision({
          cwd: capture.cwd,
          goalId: request.expected?.goalId,
          loopxAgentId: request.expected?.loopxAgentId,
        })
        if (!this.captureIsCurrent(capture)) {
          return { kind: 'fault', code: 'session_unavailable' }
        }
        if (revision !== request.sourceRevision) {
          return { kind: 'source_changed', sessionEventSeq: cursor }
        }
      } catch {
        return { kind: 'fault', code: 'session_unavailable' }
      }
      if (receipt.kind === 'timeout' || Date.now() >= deadline) {
        return { kind: 'timeout', sessionEventSeq: cursor }
      }
    }
    return { kind: 'fault', code: 'session_unavailable' }
  }

  private actionAdmission(): ActionAdmission {
    const mutation = deferred()
    const completion = deferred()
    return {
      mutationSettled: mutation.promise,
      completion: completion.promise,
      resolveMutation: () => { mutation.resolve() },
      resolveCompletion: () => { completion.resolve() },
    }
  }

  private async actionResponse(
    request: Extract<GoalBarRequestV1, { readonly op: ActionOp }>,
    signal: AbortSignal,
  ): Promise<Extract<GoalBarResponseV1, { readonly op: ActionOp }>> {
    if (this.activeAction !== undefined) {
      return {
        v: GOALBAR_RESPONSE_VERSION,
        op: request.op,
        sessionId: request.sessionId,
        result: { kind: 'rejected', code: 'action_in_flight' },
      }
    }
    const admission = this.actionAdmission()
    this.activeAction = admission
    let result: GoalBarActionResultV1
    try {
      result = await this.executeAction(request, signal, admission)
    } finally {
      admission.resolveMutation()
      if (this.activeAction === admission) this.activeAction = undefined
      admission.resolveCompletion()
    }
    return {
      v: GOALBAR_RESPONSE_VERSION,
      op: request.op,
      sessionId: request.sessionId,
      result,
    }
  }

  private async executeAction(
    request: Extract<GoalBarRequestV1, { readonly op: ActionOp }>,
    signal: AbortSignal,
    admission: ActionAdmission,
  ): Promise<GoalBarActionResultV1> {
    const capture = this.capture(request.sessionId)
    if (capture === undefined) {
      return { kind: 'rejected', code: 'binding_validation_failed' }
    }
    const validationSignal = this.combinedSignal(signal)
    let command: LoopXCommand
    try {
      command = await this.commandResolver(validationSignal)
    } catch {
      return { kind: 'rejected', code: 'binding_validation_failed' }
    }
    if (validationSignal.aborted || !this.captureIsCurrent(capture)) {
      return { kind: 'rejected', code: 'binding_validation_failed' }
    }

    const cliOptions = {
      command,
      cwd: capture.cwd,
      runner: this.runner,
      signal: validationSignal,
      env: this.env,
      retryDelaysMs: this.retryDelaysMs,
    }
    const binding = await readGoalBarBinding(
      cliOptions,
      String(capture.agent.id),
    )
    if (binding.kind === 'ambiguous') {
      this.logAmbiguity(request.sessionId, binding.uniquePairCount)
    }
    if (validationSignal.aborted || !this.captureIsCurrent(capture)) {
      return { kind: 'rejected', code: 'binding_validation_failed' }
    }
    if (binding.kind !== 'bound') {
      return { kind: 'rejected', code: 'binding_validation_failed' }
    }
    if (binding.goalId !== request.expected.goalId
      || binding.loopxAgentId !== request.expected.loopxAgentId) {
      return { kind: 'rejected', code: 'binding_mismatch' }
    }

    const activation = await readGoalBarActivation(cliOptions, binding.goalId)
    if (validationSignal.aborted || !this.captureIsCurrent(capture)) {
      return { kind: 'rejected', code: 'binding_validation_failed' }
    }
    const actionable = activation.kind === 'value'
      && (request.op === 'start'
        ? activation.goalActivation === 'stopped' && capture.agent.status === 'idle'
        : activation.goalActivation === 'active')
    if (!actionable) return { kind: 'rejected', code: 'not_actionable' }

    // Last identity/status fence before the child is synchronously launched.
    if (validationSignal.aborted
      || !this.captureIsCurrent(capture)
      || (request.op === 'start' && capture.agent.status !== 'idle')) {
      return { kind: 'rejected', code: 'binding_validation_failed' }
    }
    const targetState = request.op === 'start' ? 'active' : 'stopped'
    const operation = request.op === 'start' ? 'resume' : 'stop'
    let verified = false
    try {
      await runJsonMutationCommand(command, [
        '--registry', GOALBAR_PROJECT_REGISTRY,
        '--format', 'json',
        'goal-lifecycle',
        '--goal-id', binding.goalId,
        '--operation', operation,
        '--actor-kind', 'owner',
        '--execute',
      ], {
        runner: this.runner,
        cwd: capture.cwd,
        env: this.env,
        timeoutMs: this.actionTimeoutMs,
        maxOutputBytes: this.actionMaxOutputBytes,
        validate: payload => decodeGoalBarLifecycleExecutionV1(
          payload,
          binding.goalId,
          targetState,
        ),
      })
      verified = true
    } catch {
      return { kind: 'unknown', code: 'operation_result_unknown' }
    } finally {
      // The runner promise settles only after child close, including forced kill.
      admission.resolveMutation()
    }
    if (!verified) return { kind: 'unknown', code: 'operation_result_unknown' }

    let driverReceipt: GoalBarDriverActionReceipt
    try {
      driverReceipt = request.op === 'start'
        ? await this.coordinator.evaluateActivatedSession(capture)
        : await this.coordinator.cancelQueued(capture)
    } catch {
      driverReceipt = { kind: 'unavailable', reason: 'driver_unavailable' }
    }

    // Browser disconnect after launch does not cancel authoritative readback.
    const postRead = await this.readResult(
      request.sessionId,
      this.disposal.signal,
    )
    if (postRead.kind !== 'present'
      || postRead.snapshot.goalId !== request.expected.goalId
      || postRead.snapshot.loopxAgentId !== request.expected.loopxAgentId) {
      return { kind: 'applied_with_warning', code: 'post_read_failed' }
    }
    if (driverReceipt.kind !== 'applied') {
      return {
        kind: 'applied_with_warning',
        code: 'driver_sync_failed',
        snapshot: postRead.snapshot,
        baseSessionEventSeq: postRead.baseSessionEventSeq,
        sourceRevision: postRead.sourceRevision,
      }
    }
    return {
      kind: 'succeeded',
      snapshot: postRead.snapshot,
      baseSessionEventSeq: postRead.baseSessionEventSeq,
      sourceRevision: postRead.sourceRevision,
    }
  }
}

export function createGoalBarService(
  options: GoalBarServiceOptions,
): GoalBarService {
  return new GoalBarService(options)
}
