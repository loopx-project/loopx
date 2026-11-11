export const GOALBAR_REQUEST_VERSION = 'loopx_goalbar_request_v2' as const
export const GOALBAR_RESPONSE_VERSION = 'loopx_goalbar_response_v2' as const

export const GOALBAR_ENDPOINTS = Object.freeze({
  read: 'goalbar/read',
  watch: 'goalbar/watch',
  start: 'goalbar/start',
  pause: 'goalbar/pause',
} as const)

export const GOALBAR_READ_FAULT_CODES = Object.freeze([
  'session_unavailable',
  'cli_unavailable',
  'binding_read_failed',
  'activation_read_failed',
  'todo_read_failed',
  'protocol_mismatch',
] as const)

export const GOALBAR_ACTION_REJECTION_CODES = Object.freeze([
  'binding_mismatch',
  'binding_validation_failed',
  'not_actionable',
  'action_in_flight',
] as const)

export const GOALBAR_CLIENT_FAULT_CODES = Object.freeze([
  'transport_error',
  'protocol_error',
] as const)

/**
 * Safe failure categories for the last automatic-continuation admission
 * attempt. They summarize the typed CLI failure without command output,
 * stderr, or any runtime path, so the GoalBar can pair each category with
 * one bounded recovery hint.
 */
export const GOALBAR_ADMISSION_FAILURE_CATEGORIES = Object.freeze([
  'cli_unavailable',
  'cli_unreachable',
  'admission_rejected',
  'invalid_response',
  'driver_error',
] as const)

export type GoalBarOpV1 = keyof typeof GOALBAR_ENDPOINTS
export type GoalBarEndpointV1 = (typeof GOALBAR_ENDPOINTS)[GoalBarOpV1]
export type GoalBarReadFaultCode = (typeof GOALBAR_READ_FAULT_CODES)[number]
export type GoalBarActionRejectionCode =
  (typeof GOALBAR_ACTION_REJECTION_CODES)[number]
export type GoalBarClientFaultCode = (typeof GOALBAR_CLIENT_FAULT_CODES)[number]
export type GoalBarAdmissionFailureCategoryV1 =
  (typeof GOALBAR_ADMISSION_FAILURE_CATEGORIES)[number]
export type GoalBarActivationV1 = 'active' | 'stopped'
export type GoalBarAgentStatusV1 = 'idle' | 'running'

/** Latest driver admission outcome: clear, or failed with one safe category. */
export type GoalBarContinuationAdmissionV1 =
  | { readonly state: 'clear' }
  | {
      readonly state: 'failed'
      readonly category: GoalBarAdmissionFailureCategoryV1
    }

export const GOALBAR_ADMISSION_CLEAR: GoalBarContinuationAdmissionV1 =
  Object.freeze({ state: 'clear' })

export interface GoalBarProgressV1 {
  readonly processed: number
  readonly remaining: number
  readonly total: number
}

export interface GoalBarSnapshotV1 {
  readonly sessionId: string
  readonly goalId: string
  readonly loopxAgentId: string
  readonly goalActivation: GoalBarActivationV1
  readonly agentStatus: GoalBarAgentStatusV1
  readonly progress: GoalBarProgressV1
  readonly continuationAdmission: GoalBarContinuationAdmissionV1
}

export interface GoalBarExpectedBindingV1 {
  readonly goalId: string
  readonly loopxAgentId: string
}

export type GoalBarRequestV1 =
  | {
      readonly v: typeof GOALBAR_REQUEST_VERSION
      readonly op: 'read'
      readonly sessionId: string
    }
  | {
      readonly v: typeof GOALBAR_REQUEST_VERSION
      readonly op: 'watch'
      readonly sessionId: string
      readonly afterSessionEventSeq: number | null
      readonly sourceRevision: string
      readonly expected: GoalBarExpectedBindingV1 | null
      readonly agentStatus: GoalBarAgentStatusV1 | null
    }
  | {
      readonly v: typeof GOALBAR_REQUEST_VERSION
      readonly op: 'start'
      readonly sessionId: string
      readonly expected: GoalBarExpectedBindingV1
    }
  | {
      readonly v: typeof GOALBAR_REQUEST_VERSION
      readonly op: 'pause'
      readonly sessionId: string
      readonly expected: GoalBarExpectedBindingV1
    }

export type GoalBarReadResultV1 =
  | {
      readonly kind: 'hidden'
      readonly reason: 'binding_missing' | 'binding_ambiguous'
      readonly baseSessionEventSeq: number | null
      readonly sourceRevision: string
    }
  | {
      readonly kind: 'present'
      readonly snapshot: GoalBarSnapshotV1
      readonly baseSessionEventSeq: number | null
      readonly sourceRevision: string
    }
  | {
      readonly kind: 'fault'
      readonly code: GoalBarReadFaultCode
      readonly baseSessionEventSeq: number | null
      readonly sourceRevision: string
    }

export type GoalBarWatchResultV1 =
  | { readonly kind: 'source_changed'; readonly sessionEventSeq: number | null }
  | {
      readonly kind: 'runtime_changed'
      readonly sessionEventSeq: number | null
      readonly agentStatus: GoalBarAgentStatusV1
    }
  | { readonly kind: 'timeout'; readonly sessionEventSeq: number | null }
  | { readonly kind: 'fault'; readonly code: 'session_unavailable' }

export type GoalBarActionResultV1 =
  | {
      readonly kind: 'succeeded'
      readonly snapshot: GoalBarSnapshotV1
      readonly baseSessionEventSeq: number | null
      readonly sourceRevision: string
    }
  | {
      readonly kind: 'rejected'
      readonly code: GoalBarActionRejectionCode
    }
  | { readonly kind: 'unknown'; readonly code: 'operation_result_unknown' }
  | {
      readonly kind: 'applied_with_warning'
      readonly code: 'driver_sync_failed'
      readonly snapshot: GoalBarSnapshotV1
      readonly baseSessionEventSeq: number | null
      readonly sourceRevision: string
    }
  | {
      readonly kind: 'applied_with_warning'
      readonly code: 'post_read_failed'
    }

export type GoalBarResponseV1 =
  | {
      readonly v: typeof GOALBAR_RESPONSE_VERSION
      readonly op: 'read'
      readonly sessionId: string
      readonly result: GoalBarReadResultV1
    }
  | {
      readonly v: typeof GOALBAR_RESPONSE_VERSION
      readonly op: 'watch'
      readonly sessionId: string
      readonly result: GoalBarWatchResultV1
    }
  | {
      readonly v: typeof GOALBAR_RESPONSE_VERSION
      readonly op: 'start'
      readonly sessionId: string
      readonly result: GoalBarActionResultV1
    }
  | {
      readonly v: typeof GOALBAR_RESPONSE_VERSION
      readonly op: 'pause'
      readonly sessionId: string
      readonly result: GoalBarActionResultV1
    }

export type GoalBarResponseFor<T extends GoalBarRequestV1> =
  T extends { readonly op: 'read' }
    ? Extract<GoalBarResponseV1, { readonly op: 'read' }>
    : T extends { readonly op: 'watch' }
      ? Extract<GoalBarResponseV1, { readonly op: 'watch' }>
      : T extends { readonly op: infer TOp extends 'start' | 'pause' }
        ? Extract<GoalBarResponseV1, { readonly op: TOp }>
        : never

const LOOPX_AGENT_ID_PATTERN = /^[a-z][a-z0-9_.:@-]{0,79}$/u
const GOAL_ID_MAX_LENGTH = 512

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

function isOneOf<T extends string>(
  value: unknown,
  choices: readonly T[],
): value is T {
  return typeof value === 'string' && choices.includes(value as T)
}

function isSequence(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}

function isCursor(value: unknown): value is number | null {
  return value === null || isSequence(value)
}

const SOURCE_REVISION_PATTERN = /^sha256:[0-9a-f]{64}$/u

export function isGoalBarSourceRevision(value: unknown): value is string {
  return typeof value === 'string' && SOURCE_REVISION_PATTERN.test(value)
}

export function isGoalBarSessionId(value: unknown): value is string {
  return typeof value === 'string'
    && value.length > 0
    && [...value].length <= 128
    && value.trim() === value
    && !/[\s\u0000-\u001f/\\'"]/u.test(value)
}

export function isGoalBarGoalId(value: unknown): value is string {
  return typeof value === 'string'
    && value.length > 0
    && [...value].length <= GOAL_ID_MAX_LENGTH
    && value.trim() === value
    && !/[\u0000-\u001f\u007f]/u.test(value)
}

function isGoalBarRevisionGoalId(value: unknown): value is string {
  return isGoalBarGoalId(value)
    && value !== '.'
    && value !== '..'
    && !value.includes('/')
    && !value.includes('\\')
}

export function isGoalBarAgentId(value: unknown): value is string {
  return typeof value === 'string' && LOOPX_AGENT_ID_PATTERN.test(value)
}

export function endpointForGoalBarOp(op: GoalBarOpV1): GoalBarEndpointV1 {
  return GOALBAR_ENDPOINTS[op]
}

export function decodeGoalBarRequestV1(
  endpoint: string,
  value: unknown,
): GoalBarRequestV1 | undefined {
  const op = (Object.entries(GOALBAR_ENDPOINTS) as Array<
    [GoalBarOpV1, GoalBarEndpointV1]
  >).find(([, candidate]) => candidate === endpoint)?.[0]
  if (op === undefined) return undefined

  if (op === 'read') {
    const input = exactRecord(value, ['v', 'op', 'sessionId'])
    return input?.v === GOALBAR_REQUEST_VERSION
      && input.op === op
      && isGoalBarSessionId(input.sessionId)
      ? { v: GOALBAR_REQUEST_VERSION, op, sessionId: input.sessionId }
      : undefined
  }

  if (op === 'watch') {
    const input = exactRecord(value, [
      'v',
      'op',
      'sessionId',
      'afterSessionEventSeq',
      'sourceRevision',
      'expected',
      'agentStatus',
    ])
    const expected = input?.expected === null
      ? null
      : exactRecord(input?.expected, ['goalId', 'loopxAgentId'])
    return input?.v === GOALBAR_REQUEST_VERSION
      && input.op === op
      && isGoalBarSessionId(input.sessionId)
      && isCursor(input.afterSessionEventSeq)
      && isGoalBarSourceRevision(input.sourceRevision)
      && (expected === null
        || (isGoalBarRevisionGoalId(expected?.goalId)
          && isGoalBarAgentId(expected.loopxAgentId)))
      && (input.agentStatus === null
        || isOneOf(input.agentStatus, ['idle', 'running'] as const))
      ? {
          v: GOALBAR_REQUEST_VERSION,
          op,
          sessionId: input.sessionId,
          afterSessionEventSeq: input.afterSessionEventSeq,
          sourceRevision: input.sourceRevision,
          expected: expected === null
            ? null
            : {
                goalId: expected.goalId as string,
                loopxAgentId: expected.loopxAgentId as string,
              },
          agentStatus: input.agentStatus,
        }
      : undefined
  }

  const input = exactRecord(value, ['v', 'op', 'sessionId', 'expected'])
  const expected = exactRecord(input?.expected, ['goalId', 'loopxAgentId'])
  return input?.v === GOALBAR_REQUEST_VERSION
    && input.op === op
    && isGoalBarSessionId(input.sessionId)
    && isGoalBarGoalId(expected?.goalId)
    && isGoalBarAgentId(expected.loopxAgentId)
    ? {
        v: GOALBAR_REQUEST_VERSION,
        op,
        sessionId: input.sessionId,
        expected: {
          goalId: expected.goalId,
          loopxAgentId: expected.loopxAgentId,
        },
      }
    : undefined
}

function decodeContinuationAdmission(
  value: unknown,
): GoalBarContinuationAdmissionV1 | undefined {
  const input = record(value)
  if (input?.state === 'clear') {
    return exactRecord(value, ['state']) !== undefined
      ? { state: 'clear' }
      : undefined
  }
  if (input?.state !== 'failed') return undefined
  const failed = exactRecord(value, ['state', 'category'])
  return failed !== undefined
    && isOneOf(failed.category, GOALBAR_ADMISSION_FAILURE_CATEGORIES)
    ? { state: 'failed', category: failed.category }
    : undefined
}

export function decodeGoalBarSnapshotV1(
  value: unknown,
  expected: {
    readonly sessionId?: string | undefined
    readonly binding?: GoalBarExpectedBindingV1 | undefined
  } = {},
): GoalBarSnapshotV1 | undefined {
  const input = exactRecord(value, [
    'sessionId',
    'goalId',
    'loopxAgentId',
    'goalActivation',
    'agentStatus',
    'progress',
    'continuationAdmission',
  ])
  const progress = exactRecord(input?.progress, ['processed', 'remaining', 'total'])
  const admission = decodeContinuationAdmission(input?.continuationAdmission)
  if (!isGoalBarSessionId(input?.sessionId)
    || !isGoalBarGoalId(input.goalId)
    || !isGoalBarAgentId(input.loopxAgentId)
    || !isOneOf(input.goalActivation, ['active', 'stopped'] as const)
    || !isOneOf(input.agentStatus, ['idle', 'running'] as const)
    || !isSequence(progress?.processed)
    || !isSequence(progress.remaining)
    || !isSequence(progress.total)
    || !Number.isSafeInteger(progress.processed + progress.remaining)
    || progress.processed + progress.remaining !== progress.total
    || admission === undefined
    || (expected.sessionId !== undefined && input.sessionId !== expected.sessionId)
    || (expected.binding !== undefined
      && (input.goalId !== expected.binding.goalId
        || input.loopxAgentId !== expected.binding.loopxAgentId))) {
    return undefined
  }
  return {
    sessionId: input.sessionId,
    goalId: input.goalId,
    loopxAgentId: input.loopxAgentId,
    goalActivation: input.goalActivation,
    agentStatus: input.agentStatus,
    progress: {
      processed: progress.processed,
      remaining: progress.remaining,
      total: progress.total,
    },
    continuationAdmission: admission,
  }
}

function decodeReadResult(
  value: unknown,
  sessionId: string,
): GoalBarReadResultV1 | undefined {
  const candidate = record(value)
  if (candidate?.kind === 'hidden') {
    const input = exactRecord(value, [
      'kind', 'reason', 'baseSessionEventSeq', 'sourceRevision',
    ])
    return input !== undefined
      && isOneOf(input.reason, ['binding_missing', 'binding_ambiguous'] as const)
      && isCursor(input.baseSessionEventSeq)
      && isGoalBarSourceRevision(input.sourceRevision)
      ? {
          kind: 'hidden',
          reason: input.reason,
          baseSessionEventSeq: input.baseSessionEventSeq,
          sourceRevision: input.sourceRevision,
        }
      : undefined
  }
  if (candidate?.kind === 'present') {
    const input = exactRecord(value, [
      'kind', 'snapshot', 'baseSessionEventSeq', 'sourceRevision',
    ])
    const snapshot = decodeGoalBarSnapshotV1(input?.snapshot, { sessionId })
    return input !== undefined
      && snapshot !== undefined
      && isCursor(input.baseSessionEventSeq)
      && isGoalBarSourceRevision(input.sourceRevision)
      ? {
          kind: 'present',
          snapshot,
          baseSessionEventSeq: input.baseSessionEventSeq,
          sourceRevision: input.sourceRevision,
        }
      : undefined
  }
  if (candidate?.kind === 'fault') {
    const input = exactRecord(value, [
      'kind', 'code', 'baseSessionEventSeq', 'sourceRevision',
    ])
    return input !== undefined
      && isOneOf(input.code, GOALBAR_READ_FAULT_CODES)
      && isCursor(input.baseSessionEventSeq)
      && isGoalBarSourceRevision(input.sourceRevision)
      ? {
          kind: 'fault',
          code: input.code,
          baseSessionEventSeq: input.baseSessionEventSeq,
          sourceRevision: input.sourceRevision,
        }
      : undefined
  }
  return undefined
}

function decodeWatchResult(
  value: unknown,
  afterSessionEventSeq: number | null,
): GoalBarWatchResultV1 | undefined {
  const candidate = record(value)
  if (candidate?.kind === 'source_changed') {
    const input = exactRecord(value, ['kind', 'sessionEventSeq'])
    return input !== undefined
      && isCursor(input.sessionEventSeq)
      && (afterSessionEventSeq === null
        || (input.sessionEventSeq !== null
          && input.sessionEventSeq >= afterSessionEventSeq))
      ? { kind: 'source_changed', sessionEventSeq: input.sessionEventSeq }
      : undefined
  }
  if (candidate?.kind === 'runtime_changed') {
    const input = exactRecord(value, ['kind', 'sessionEventSeq', 'agentStatus'])
    return input !== undefined
      && isCursor(input.sessionEventSeq)
      && (afterSessionEventSeq === null
        || (input.sessionEventSeq !== null
          && input.sessionEventSeq >= afterSessionEventSeq))
      && isOneOf(input.agentStatus, ['idle', 'running'] as const)
      ? {
          kind: 'runtime_changed',
          sessionEventSeq: input.sessionEventSeq,
          agentStatus: input.agentStatus,
        }
      : undefined
  }
  if (candidate?.kind === 'timeout') {
    const input = exactRecord(value, ['kind', 'sessionEventSeq'])
    return input !== undefined
      && isCursor(input.sessionEventSeq)
      && (afterSessionEventSeq === null
        || (input.sessionEventSeq !== null
          && input.sessionEventSeq >= afterSessionEventSeq))
      ? { kind: 'timeout', sessionEventSeq: input.sessionEventSeq }
      : undefined
  }
  if (candidate?.kind === 'fault') {
    const input = exactRecord(value, ['kind', 'code'])
    return input?.code === 'session_unavailable'
      ? { kind: 'fault', code: 'session_unavailable' }
      : undefined
  }
  return undefined
}

function decodeActionResult(
  value: unknown,
  sessionId: string,
  binding: GoalBarExpectedBindingV1,
): GoalBarActionResultV1 | undefined {
  const candidate = record(value)
  if (candidate?.kind === 'succeeded') {
    const input = exactRecord(value, [
      'kind', 'snapshot', 'baseSessionEventSeq', 'sourceRevision',
    ])
    const snapshot = decodeGoalBarSnapshotV1(input?.snapshot, { sessionId, binding })
    return input !== undefined
      && snapshot !== undefined
      && isCursor(input.baseSessionEventSeq)
      && isGoalBarSourceRevision(input.sourceRevision)
      ? {
          kind: 'succeeded',
          snapshot,
          baseSessionEventSeq: input.baseSessionEventSeq,
          sourceRevision: input.sourceRevision,
        }
      : undefined
  }
  if (candidate?.kind === 'rejected') {
    const input = exactRecord(value, ['kind', 'code'])
    return input !== undefined && isOneOf(input.code, GOALBAR_ACTION_REJECTION_CODES)
      ? { kind: 'rejected', code: input.code }
      : undefined
  }
  if (candidate?.kind === 'unknown') {
    const input = exactRecord(value, ['kind', 'code'])
    return input?.code === 'operation_result_unknown'
      ? { kind: 'unknown', code: 'operation_result_unknown' }
      : undefined
  }
  if (candidate?.kind === 'applied_with_warning') {
    if (candidate.code === 'post_read_failed') {
      const input = exactRecord(value, ['kind', 'code'])
      return input?.code === 'post_read_failed'
        ? { kind: 'applied_with_warning', code: 'post_read_failed' }
        : undefined
    }
    const input = exactRecord(value, [
      'kind', 'code', 'snapshot', 'baseSessionEventSeq', 'sourceRevision',
    ])
    const snapshot = decodeGoalBarSnapshotV1(input?.snapshot, { sessionId, binding })
    return input?.code === 'driver_sync_failed'
      && snapshot !== undefined
      && isCursor(input.baseSessionEventSeq)
      && isGoalBarSourceRevision(input.sourceRevision)
      ? {
          kind: 'applied_with_warning',
          code: 'driver_sync_failed',
          snapshot,
          baseSessionEventSeq: input.baseSessionEventSeq,
          sourceRevision: input.sourceRevision,
        }
      : undefined
  }
  return undefined
}

export function decodeGoalBarResponseV1<T extends GoalBarRequestV1>(
  request: T,
  value: unknown,
): GoalBarResponseFor<T> | undefined {
  const input = exactRecord(value, ['v', 'op', 'sessionId', 'result'])
  if (input?.v !== GOALBAR_RESPONSE_VERSION
    || input.op !== request.op
    || input.sessionId !== request.sessionId) return undefined

  let result: GoalBarReadResultV1 | GoalBarWatchResultV1 | GoalBarActionResultV1 | undefined
  if (request.op === 'read') {
    result = decodeReadResult(input.result, request.sessionId)
  } else if (request.op === 'watch') {
    result = decodeWatchResult(input.result, request.afterSessionEventSeq)
  } else {
    result = decodeActionResult(input.result, request.sessionId, request.expected)
  }
  return result === undefined
    ? undefined
    : {
        v: GOALBAR_RESPONSE_VERSION,
        op: request.op,
        sessionId: request.sessionId,
        result,
      } as GoalBarResponseFor<T>
}
