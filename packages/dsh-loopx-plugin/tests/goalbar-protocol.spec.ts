import { describe, expect, it } from 'vitest'
import {
  GOALBAR_ADMISSION_FAILURE_CATEGORIES,
  GOALBAR_ENDPOINTS,
  GOALBAR_READ_FAULT_CODES,
  GOALBAR_REQUEST_VERSION,
  GOALBAR_RESPONSE_VERSION,
  decodeGoalBarRequestV1,
  decodeGoalBarResponseV1,
  decodeGoalBarSnapshotV1,
} from '../src/goalbar/protocol.ts'
import type { GoalBarRequestV1, GoalBarSnapshotV1 } from '../src/goalbar/protocol.ts'

const sessionId = 'dsh-session-1'
const binding = { goalId: 'goal-one', loopxAgentId: 'codex-main-control' }
const revision = `sha256:${'a'.repeat(64)}`
const snapshot: GoalBarSnapshotV1 = {
  sessionId,
  ...binding,
  goalActivation: 'active',
  agentStatus: 'idle',
  progress: { processed: 2, remaining: 3, total: 5 },
  continuationAdmission: { state: 'clear' },
}
function response(request: GoalBarRequestV1, result: unknown): Record<string, unknown> {
  return { v: GOALBAR_RESPONSE_VERSION, op: request.op, sessionId, result }
}

describe('GoalBar request V2', () => {
  it('decodes the closed V2 endpoint shapes', () => {
    expect(decodeGoalBarRequestV1(GOALBAR_ENDPOINTS.read, {
      v: GOALBAR_REQUEST_VERSION, op: 'read', sessionId,
    })).toEqual({ v: GOALBAR_REQUEST_VERSION, op: 'read', sessionId })

    const watch = {
      v: GOALBAR_REQUEST_VERSION,
      op: 'watch',
      sessionId,
      afterSessionEventSeq: 7,
      sourceRevision: revision,
      expected: binding,
      agentStatus: 'idle',
    } as const
    expect(decodeGoalBarRequestV1(GOALBAR_ENDPOINTS.watch, watch)).toEqual(watch)
    expect(decodeGoalBarRequestV1(GOALBAR_ENDPOINTS.watch, {
      ...watch, expected: null, agentStatus: null,
    })).toEqual({ ...watch, expected: null, agentStatus: null })

    for (const op of ['start', 'pause'] as const) {
      expect(decodeGoalBarRequestV1(GOALBAR_ENDPOINTS[op], {
        v: GOALBAR_REQUEST_VERSION, op, sessionId, expected: binding,
      })).toEqual({ v: GOALBAR_REQUEST_VERSION, op, sessionId, expected: binding })
    }
  })

  it('rejects V1/V2 mixing, malformed revisions, cursors, bindings, and extra fields', () => {
    const watch = {
      v: GOALBAR_REQUEST_VERSION,
      op: 'watch',
      sessionId,
      afterSessionEventSeq: 7,
      sourceRevision: revision,
      expected: binding,
      agentStatus: 'idle',
    } as const
    for (const change of [
      { v: 'loopx_goalbar_request_v1' },
      { afterSessionEventSeq: -1 },
      { afterSessionEventSeq: Number.MAX_SAFE_INTEGER + 1 },
      { sourceRevision: 'a'.repeat(64) },
      { sourceRevision: `sha256:${'A'.repeat(64)}` },
      { expected: { ...binding, rawPath: '/private/state' } },
      { expected: { goalId: '../goal', loopxAgentId: binding.loopxAgentId } },
      { agentStatus: 'paused' },
      { debug: true },
    ]) {
      expect(decodeGoalBarRequestV1(GOALBAR_ENDPOINTS.watch, {
        ...watch, ...change,
      })).toBeUndefined()
    }
    expect(decodeGoalBarRequestV1(GOALBAR_ENDPOINTS.read, {
      v: GOALBAR_REQUEST_VERSION, op: 'watch', sessionId,
    })).toBeUndefined()
  })
})

describe('GoalBar response V2', () => {
  const readRequest = {
    v: GOALBAR_REQUEST_VERSION, op: 'read', sessionId,
  } as const
  const watchRequest = {
    v: GOALBAR_REQUEST_VERSION,
    op: 'watch',
    sessionId,
    afterSessionEventSeq: 7,
    sourceRevision: revision,
    expected: binding,
    agentStatus: 'idle',
  } as const
  const actionRequest = {
    v: GOALBAR_REQUEST_VERSION, op: 'pause', sessionId, expected: binding,
  } as const

  it('decodes all closed read branches with one observation anchor', () => {
    expect(decodeGoalBarResponseV1(readRequest, response(readRequest, {
      kind: 'hidden',
      reason: 'binding_missing',
      baseSessionEventSeq: null,
      sourceRevision: revision,
    }))?.result).toEqual({
      kind: 'hidden',
      reason: 'binding_missing',
      baseSessionEventSeq: null,
      sourceRevision: revision,
    })
    expect(decodeGoalBarResponseV1(readRequest, response(readRequest, {
      kind: 'present', snapshot, baseSessionEventSeq: 12, sourceRevision: revision,
    }))?.result).toEqual({
      kind: 'present', snapshot, baseSessionEventSeq: 12, sourceRevision: revision,
    })
    for (const code of GOALBAR_READ_FAULT_CODES) {
      expect(decodeGoalBarResponseV1(readRequest, response(readRequest, {
        kind: 'fault', code, baseSessionEventSeq: 12, sourceRevision: revision,
      }))?.result).toEqual({
        kind: 'fault', code, baseSessionEventSeq: 12, sourceRevision: revision,
      })
    }
    expect(decodeGoalBarResponseV1(readRequest, response(readRequest, {
      kind: 'present', snapshot, baseSessionEventSeq: 12,
      sourceRevision: revision, raw: '/private/state',
    }))).toBeUndefined()
  })

  it('decodes source, runtime, and timeout results with non-regressing cursors', () => {
    expect(decodeGoalBarResponseV1(watchRequest, response(watchRequest, {
      kind: 'source_changed', sessionEventSeq: 8,
    }))?.result).toEqual({ kind: 'source_changed', sessionEventSeq: 8 })
    expect(decodeGoalBarResponseV1(watchRequest, response(watchRequest, {
      kind: 'runtime_changed', sessionEventSeq: 7, agentStatus: 'running',
    }))?.result).toEqual({
      kind: 'runtime_changed', sessionEventSeq: 7, agentStatus: 'running',
    })
    expect(decodeGoalBarResponseV1(watchRequest, response(watchRequest, {
      kind: 'timeout', sessionEventSeq: 10,
    }))?.result).toEqual({ kind: 'timeout', sessionEventSeq: 10 })
    for (const result of [
      { kind: 'source_changed', sessionEventSeq: 6 },
      { kind: 'runtime_changed', sessionEventSeq: 6, agentStatus: 'running' },
      { kind: 'timeout', sessionEventSeq: 6 },
      { kind: 'runtime_changed', sessionEventSeq: 8, agentStatus: 'paused' },
    ]) {
      expect(decodeGoalBarResponseV1(watchRequest, response(watchRequest, result)))
        .toBeUndefined()
    }
  })

  it('requires fresh observation anchors on action snapshots', () => {
    for (const result of [
      {
        kind: 'succeeded', snapshot, baseSessionEventSeq: 12,
        sourceRevision: revision,
      },
      {
        kind: 'applied_with_warning', code: 'driver_sync_failed', snapshot,
        baseSessionEventSeq: 13, sourceRevision: revision,
      },
    ]) {
      expect(decodeGoalBarResponseV1(actionRequest, response(actionRequest, result))?.result)
        .toEqual(result)
    }
    expect(decodeGoalBarResponseV1(actionRequest, response(actionRequest, {
      kind: 'succeeded', snapshot: { ...snapshot, goalId: 'other' },
      baseSessionEventSeq: 12, sourceRevision: revision,
    }))).toBeUndefined()
  })
})

describe('GoalBar snapshot V1 payload', () => {
  it('remains closed and internally consistent', () => {
    expect(decodeGoalBarSnapshotV1(snapshot, { sessionId, binding })).toEqual(snapshot)
    expect(decodeGoalBarSnapshotV1({
      ...snapshot,
      progress: { processed: 2, remaining: 2, total: 5 },
    })).toBeUndefined()
    expect(decodeGoalBarSnapshotV1({ ...snapshot, rawPayload: '/private/state' }))
      .toBeUndefined()
  })

  it('decodes every closed admission failure category and nothing else', () => {
    for (const category of GOALBAR_ADMISSION_FAILURE_CATEGORIES) {
      const failed = {
        ...snapshot,
        continuationAdmission: { state: 'failed', category },
      }
      expect(decodeGoalBarSnapshotV1(failed)).toEqual(failed)
    }
    for (const admission of [
      undefined,
      { state: 'failed' },
      { state: 'clear', category: 'invalid_response' },
      { state: 'failed', category: 'unexpected_category' },
      { state: 'failed', category: 'invalid_response', detail: '/private/stderr' },
      { state: 'paused' },
    ]) {
      expect(decodeGoalBarSnapshotV1({ ...snapshot, continuationAdmission: admission }))
        .toBeUndefined()
    }
  })
})
