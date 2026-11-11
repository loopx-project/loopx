import type { Agent } from '@deepseek-ai/dsh-agent'
import type { Session, SessionEvent } from '@deepseek-ai/dsh-session'
import type {
  GoalBarAgentStatusV1,
  GoalBarContinuationAdmissionV1,
} from './protocol.ts'
import { GOALBAR_ADMISSION_CLEAR } from './protocol.ts'

export interface GoalBarSessionCapture { readonly agent: Agent; readonly session: Session }
export type GoalBarSessionNotification =
  | { readonly kind: 'candidate'; readonly sessionId: string; readonly sessionEventSeq: number }
  | { readonly kind: 'runtime_changed'; readonly sessionId: string; readonly agentStatus: GoalBarAgentStatusV1 }
  | { readonly kind: 'admission_changed'; readonly sessionId: string }
export type GoalBarSessionWaitReceipt = GoalBarSessionNotification
  | { readonly kind: 'timeout'; readonly sessionEventSeq: number | null }
  | { readonly kind: 'session_unavailable' }
  | { readonly kind: 'aborted' }
  | { readonly kind: 'service_disposed' }
export type GoalBarDriverActionReceipt = { readonly kind: 'applied' } | {
  readonly kind: 'unavailable'
  readonly reason: 'driver_unavailable' | 'session_unavailable' | 'evaluation_unavailable'
}
export interface GoalBarDriverBridge {
  evaluateActivatedSession(capture: GoalBarSessionCapture): Promise<GoalBarDriverActionReceipt>
  cancelQueued(capture: GoalBarSessionCapture): Promise<GoalBarDriverActionReceipt>
  readContinuationAdmission(
    capture: GoalBarSessionCapture,
  ): GoalBarContinuationAdmissionV1
}
export interface GoalBarSessionWaitOptions {
  readonly signal: AbortSignal
  readonly timeoutMs: number
  readonly observedAgentStatus: GoalBarAgentStatusV1 | null
  readonly isSessionCurrent: () => boolean
  readonly getAgentStatus: () => GoalBarAgentStatusV1 | undefined
}
export interface GoalBarWatchService {
  waitForSessionChange(
    session: Session,
    afterSessionEventSeq: number | null,
    options: GoalBarSessionWaitOptions,
  ): Promise<GoalBarSessionWaitReceipt>
  dispose(): void
}
interface WatchServiceState { disposed: boolean }
interface PendingWaiter {
  readonly service: WatchServiceState
  readonly session: Session
  readonly afterSessionEventSeq: number | null
  readonly options: GoalBarSessionWaitOptions
  readonly resolve: (receipt: GoalBarSessionWaitReceipt) => void
  readonly onAbort: () => void
  readonly timer: ReturnType<typeof setTimeout>
  settled: boolean
}
function isSequence(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}
function isNewer(sequence: number, cursor: number | null): boolean {
  return cursor === null || sequence > cursor
}
function isCurrent(predicate: () => boolean): boolean {
  try { return predicate() } catch { return false }
}
function currentStatus(options: GoalBarSessionWaitOptions): GoalBarAgentStatusV1 | undefined {
  try { return options.getAgentStatus() } catch { return undefined }
}
function runtimeReceipt(
  session: Session,
  options: GoalBarSessionWaitOptions,
): GoalBarSessionNotification | undefined {
  const status = currentStatus(options)
  return status !== undefined && status !== options.observedAgentStatus
    ? { kind: 'runtime_changed', sessionId: String(session.id), agentStatus: status }
    : undefined
}
export function latestSessionEventSeq(session: Session): number | null {
  const events = session.snapshotEvents()
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const seq = events[index]?.seq
    if (isSequence(seq)) return seq
  }
  return null
}
export function latestGoalBarCandidateSeq(session: Session): number | null {
  const events = session.snapshotEvents()
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if ((event?.type === 'step/end' || event?.type === 'turn/end') && isSequence(event.seq)) {
      return event.seq
    }
  }
  return null
}

/** Exact-Session event coordinator; it owns waiters, never business snapshots. */
export class GoalBarCoordinator {
  private readonly waiters = new Set<PendingWaiter>()
  private driverBridge?: GoalBarDriverBridge | undefined

  openWatchService(): GoalBarWatchService {
    const service: WatchServiceState = { disposed: false }
    return {
      waitForSessionChange: (session, cursor, options) => this.waitForSessionChange(
        service, session, cursor, options,
      ),
      dispose: () => {
        if (service.disposed) return
        service.disposed = true
        for (const waiter of [...this.waiters]) {
          if (waiter.service === service) this.settle(waiter, { kind: 'service_disposed' })
        }
      },
    }
  }

  publishSessionCandidate(
    session: Session,
    event: SessionEvent<'step/end'> | SessionEvent<'turn/end'>,
  ): void {
    if (!isSequence(event.seq)) return
    for (const waiter of [...this.waiters]) {
      if (waiter.session !== session || !isNewer(event.seq, waiter.afterSessionEventSeq)) continue
      this.settle(waiter, isCurrent(waiter.options.isSessionCurrent)
        ? { kind: 'candidate', sessionId: String(session.id), sessionEventSeq: event.seq }
        : { kind: 'session_unavailable' })
    }
  }

  publishAgentStatus(session: Session, status: GoalBarAgentStatusV1): void {
    for (const waiter of [...this.waiters]) {
      if (waiter.session !== session || waiter.options.observedAgentStatus === status) continue
      this.settle(waiter, isCurrent(waiter.options.isSessionCurrent)
        ? { kind: 'runtime_changed', sessionId: String(session.id), agentStatus: status }
        : { kind: 'session_unavailable' })
    }
  }

  /** Driver admission state is process-local and carries no event sequence. */
  publishAdmissionChange(session: Session): void {
    for (const waiter of [...this.waiters]) {
      if (waiter.session !== session) continue
      this.settle(waiter, isCurrent(waiter.options.isSessionCurrent)
        ? { kind: 'admission_changed', sessionId: String(session.id) }
        : { kind: 'session_unavailable' })
    }
  }

  invalidateSession(session: Session): void {
    for (const waiter of [...this.waiters]) {
      if (waiter.session === session) this.settle(waiter, { kind: 'session_unavailable' })
    }
  }
  registerDriverBridge(bridge: GoalBarDriverBridge): () => void {
    this.driverBridge = bridge
    let registered = true
    return () => {
      if (!registered) return
      registered = false
      if (this.driverBridge === bridge) this.driverBridge = undefined
    }
  }
  evaluateActivatedSession(capture: GoalBarSessionCapture): Promise<GoalBarDriverActionReceipt> {
    return this.invokeDriver('evaluateActivatedSession', capture)
  }
  cancelQueued(capture: GoalBarSessionCapture): Promise<GoalBarDriverActionReceipt> {
    return this.invokeDriver('cancelQueued', capture)
  }
  readContinuationAdmission(
    capture: GoalBarSessionCapture,
  ): GoalBarContinuationAdmissionV1 {
    if (capture.agent.session !== capture.session) return GOALBAR_ADMISSION_CLEAR
    const bridge = this.driverBridge
    if (bridge === undefined) return GOALBAR_ADMISSION_CLEAR
    try {
      return bridge.readContinuationAdmission(capture)
    } catch {
      return GOALBAR_ADMISSION_CLEAR
    }
  }

  private waitForSessionChange(
    service: WatchServiceState,
    session: Session,
    afterSessionEventSeq: number | null,
    options: GoalBarSessionWaitOptions,
  ): Promise<GoalBarSessionWaitReceipt> {
    if (service.disposed) return Promise.resolve({ kind: 'service_disposed' })
    if (options.signal.aborted) return Promise.resolve({ kind: 'aborted' })
    if (!isCurrent(options.isSessionCurrent)) return Promise.resolve({ kind: 'session_unavailable' })
    const immediate = latestGoalBarCandidateSeq(session)
    if (immediate !== null && isNewer(immediate, afterSessionEventSeq)) {
      return Promise.resolve({ kind: 'candidate', sessionId: String(session.id), sessionEventSeq: immediate })
    }
    const runtime = runtimeReceipt(session, options)
    if (runtime !== undefined) return Promise.resolve(runtime)
    return new Promise(resolveReceipt => {
      const timeoutMs = Number.isSafeInteger(options.timeoutMs) && options.timeoutMs >= 0
        ? options.timeoutMs : 0
      let waiter: PendingWaiter
      const onAbort = (): void => { this.settle(waiter, { kind: 'aborted' }) }
      const timer = setTimeout(() => {
        this.settle(waiter, isCurrent(options.isSessionCurrent)
          ? { kind: 'timeout', sessionEventSeq: latestSessionEventSeq(session) ?? afterSessionEventSeq }
          : { kind: 'session_unavailable' })
      }, timeoutMs)
      waiter = { service, session, afterSessionEventSeq, options, resolve: resolveReceipt, onAbort, timer, settled: false }
      this.waiters.add(waiter)
      options.signal.addEventListener('abort', onAbort, { once: true })
      if (service.disposed) return this.settle(waiter, { kind: 'service_disposed' })
      if (options.signal.aborted) return this.settle(waiter, { kind: 'aborted' })
      if (!isCurrent(options.isSessionCurrent)) return this.settle(waiter, { kind: 'session_unavailable' })
      const rechecked = latestGoalBarCandidateSeq(session)
      if (rechecked !== null && isNewer(rechecked, afterSessionEventSeq)) {
        return this.settle(waiter, { kind: 'candidate', sessionId: String(session.id), sessionEventSeq: rechecked })
      }
      const recheckedRuntime = runtimeReceipt(session, options)
      if (recheckedRuntime !== undefined) this.settle(waiter, recheckedRuntime)
    })
  }
  private settle(waiter: PendingWaiter, receipt: GoalBarSessionWaitReceipt): void {
    if (waiter.settled) return
    waiter.settled = true
    this.waiters.delete(waiter)
    clearTimeout(waiter.timer)
    waiter.options.signal.removeEventListener('abort', waiter.onAbort)
    waiter.resolve(receipt)
  }
  private async invokeDriver(
    operation: 'evaluateActivatedSession' | 'cancelQueued',
    capture: GoalBarSessionCapture,
  ): Promise<GoalBarDriverActionReceipt> {
    if (capture.agent.session !== capture.session) return { kind: 'unavailable', reason: 'session_unavailable' }
    const bridge = this.driverBridge
    if (bridge === undefined) return { kind: 'unavailable', reason: 'driver_unavailable' }
    try { return await bridge[operation](capture) } catch {
      return { kind: 'unavailable', reason: 'driver_unavailable' }
    }
  }
}
export const goalBarCoordinator = new GoalBarCoordinator()
