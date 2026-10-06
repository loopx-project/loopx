import { describe, expect, it } from 'vitest'
import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type {
  ConnectionFetchRoute,
  ConnectionRpcHandler,
  HostConnectionHandle,
} from '@deepseek-ai/dsh-client-connection'
import {
  apply,
  inject,
  name,
} from '../src/index.ts'
import {
  createGoalBarConnectionHandler,
  registerGoalBarConnectionTransport,
  registerGoalBarConnectionRpc,
} from '../src/goalbar/connection-rpc.ts'
import type {
  GoalBarRequestV1,
  GoalBarResponseV1,
} from '../src/goalbar/protocol.ts'
import { unavailableGoalBarSourceRevision } from '../src/goalbar/read-model.ts'
import type { GoalBarServiceHandle } from '../src/goalbar/service.ts'

const sessionId = 'session-fixture'
const sourceRevision = `sha256:${'0'.repeat(64)}`

function readRequest(): Extract<GoalBarRequestV1, { readonly op: 'read' }> {
  return { v: 'loopx_goalbar_request_v2', op: 'read', sessionId }
}

function readResponse(): Extract<GoalBarResponseV1, { readonly op: 'read' }> {
  return {
    v: 'loopx_goalbar_response_v2',
    op: 'read',
    sessionId,
    result: {
      kind: 'hidden',
      reason: 'binding_missing',
      baseSessionEventSeq: null,
      sourceRevision,
    },
  }
}

function connectionCapture(): {
  readonly connection: Pick<HostConnectionHandle, 'rpc'>
  readonly calls: Array<{
    channel: string
    handler: ConnectionRpcHandler
  }>
  readonly disposed: () => number
} {
  const calls: Array<{
    channel: string
    handler: ConnectionRpcHandler
  }> = []
  let disposeCalls = 0
  const connection = {
    rpc: {
      handle(channel: string, handler: ConnectionRpcHandler) {
        calls.push({ channel, handler })
        return async () => { disposeCalls += 1 }
      },
      intercept() {
        throw new Error('not used')
      },
    },
  } satisfies Pick<HostConnectionHandle, 'rpc'>
  return { connection, calls, disposed: () => disposeCalls }
}

function sharedApiConnectionCapture(): {
  readonly connection: Pick<HostConnectionHandle, 'fetch'>
  readonly routes: ConnectionFetchRoute[]
  readonly disposed: () => number
} {
  const routes: ConnectionFetchRoute[] = []
  let disposeCalls = 0
  const connection = {
    fetch: {
      register(route: ConnectionFetchRoute) {
        routes.push(route)
        return async () => { disposeCalls += 1 }
      },
    },
  } satisfies Pick<HostConnectionHandle, 'fetch'>
  return { connection, routes, disposed: () => disposeCalls }
}

describe('GoalBar Connection carrier', () => {
  it('registers the real handler at the authenticated /loopx channel and returns its disposer', async () => {
    const capture = connectionCapture()
    const service: GoalBarServiceHandle = {
      handle: async () => readResponse(),
      dispose: async () => {},
    }
    const dispose = registerGoalBarConnectionRpc(capture.connection, service)

    expect(capture.calls).toHaveLength(1)
    expect(capture.calls[0]).toMatchObject({
      channel: '/loopx',
    })
    const result = await capture.calls[0]?.handler(
      'goalbar/read',
      readRequest(),
      new AbortController().signal,
      { id: 'operator-fixture' as Parameters<ConnectionRpcHandler>[3]['id'],
        ctx: {} as Context, dispose: async () => {} },
    )
    expect(result).toEqual({ ok: true, value: readResponse() })

    await dispose()
    expect(capture.disposed()).toBe(1)
  })

  it('uses DSH 0.1.5 authenticated shared-API routes without caller WebServer access', async () => {
    const capture = sharedApiConnectionCapture()
    const service: GoalBarServiceHandle = {
      handle: async () => readResponse(),
      dispose: async () => {},
    }
    const dispose = registerGoalBarConnectionTransport(capture.connection, service)

    expect(capture.routes.map(route => route.path)).toEqual([
      '/api/loopx.goalbar',
    ])
    const readRoute = capture.routes[0]
    expect(readRoute).toMatchObject({
      methods: ['POST'],
      requestBody: 'buffered',
    })
    const response = await readRoute?.fetch(new Request(
      'http://dsh.internal/api/loopx.goalbar',
      {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          type: 'client-request',
          rpcId: 'rpc-shared-api',
          method: 'loopx.goalbar',
          payload: readRequest(),
        }),
      },
    ))
    expect(response?.status).toBe(200)
    expect(await response?.json()).toEqual({
      type: 'server-response',
      rpcId: 'rpc-shared-api',
      result: { ok: true, value: readResponse() },
    })

    await dispose()
    expect(capture.disposed()).toBe(1)
  })

  it('uses a fixed bad-request carrier for hostile shape and endpoint/op mismatch', async () => {
    let serviceCalls = 0
    const handler = createGoalBarConnectionHandler({
      handle: async () => {
        serviceCalls += 1
        return readResponse()
      },
      dispose: async () => {},
    })
    const hostileText = '/private/project raw stderr candidate-goal'
    const hostile = new Proxy({}, {
      ownKeys() { throw new Error(hostileText) },
    })

    for (const [endpoint, payload] of [
      ['goalbar/read', { ...readRequest(), extra: hostileText }],
      ['goalbar/watch', readRequest()],
      ['goalbar/read', hostile],
    ] as const) {
      const result = await handler(
        endpoint,
        payload,
        new AbortController().signal,
      )
      expect(result).toEqual({
        ok: false,
        error: {
          code: 'bad-request',
          message: 'invalid LoopX GoalBar request',
          details: { issues: [] },
        },
      })
      expect(JSON.stringify(result)).not.toContain(hostileText)
    }
    expect(serviceCalls).toBe(0)
  })

  it('contains Host exceptions inside a valid V2 business response', async () => {
    const privateError = [
      '/workspace/project',
      'raw stdout',
      'raw stderr',
      'candidate-goal',
      'Error: private stack',
    ].join(' | ')
    const handler = createGoalBarConnectionHandler({
      handle: async () => { throw new Error(privateError) },
      dispose: async () => {},
    })
    const result = await handler(
      'goalbar/read',
      readRequest(),
      new AbortController().signal,
    )

    expect(result).toEqual({
      ok: true,
      value: {
        v: 'loopx_goalbar_response_v2',
        op: 'read',
        sessionId,
        result: {
          kind: 'fault',
          code: 'protocol_mismatch',
          baseSessionEventSeq: null,
          sourceRevision: unavailableGoalBarSourceRevision(),
        },
      },
    })
    const wire = JSON.stringify(result)
    for (const fragment of privateError.split(' | ')) {
      expect(wire).not.toContain(fragment)
    }
  })

  it('passes the exact request AbortSignal to the business handler', async () => {
    let observedSignal: AbortSignal | undefined
    const handler = createGoalBarConnectionHandler({
      handle: async (request, signal) => {
        observedSignal = signal
        await new Promise<void>(resolve => {
          if (signal.aborted) resolve()
          else signal.addEventListener('abort', () => { resolve() }, { once: true })
        })
        return {
          v: 'loopx_goalbar_response_v2',
          op: request.op,
          sessionId: request.sessionId,
          result: { kind: 'fault', code: 'session_unavailable' },
        } as GoalBarResponseV1
      },
      dispose: async () => {},
    })
    const controller = new AbortController()
    const call = handler(
      'goalbar/watch',
      {
        v: 'loopx_goalbar_request_v2',
        op: 'watch',
        sessionId,
        afterSessionEventSeq: null,
        sourceRevision,
        expected: null,
        agentStatus: 'idle',
      },
      controller.signal,
    )
    controller.abort()
    expect((await call).ok).toBe(true)
    expect(observedSignal).toBe(controller.signal)
  })
})

describe('package-root GoalBar Host', () => {
  it('keeps the package-root dependency set stable across DSH carriers', () => {
    expect(inject).toEqual(['agents', 'connection', 'loopxBootstrap'])
  })

  it('constructs one real service, registers authority, cancels watch, and disposes', async () => {
    const capture = sharedApiConnectionCapture()
    const session = {
      id: sessionId,
      header: { version: 0, id: sessionId, createdAt: 1, cwd: '/fixture/project' },
      snapshotEvents: () => [],
      surface: { nodes: [] },
    }
    const agent = {
      id: sessionId,
      session,
      status: 'idle',
    } as unknown as Agent
    let cleanup: (() => Promise<void>) | undefined
    const ctx = {
      agents: { get: (id: string) => id === sessionId ? agent : undefined },
      connection: capture.connection,
      logger: { warn() {} },
      effect(effect: () => unknown) {
        cleanup = effect() as () => Promise<void>
        return async () => { await cleanup?.() }
      },
    } as unknown as Context

    expect(name).toBe('dsh-loopx-plugin')
    apply(ctx)
    expect(capture.routes[0]).toMatchObject({ path: '/api/loopx.goalbar' })

    const controller = new AbortController()
    const call = capture.routes[0]?.fetch(new Request('http://fixture/api/loopx.goalbar', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        type: 'client-request', rpcId: 'watch-fixture', method: 'loopx.goalbar',
        payload: {
          v: 'loopx_goalbar_request_v2', op: 'watch', sessionId,
          afterSessionEventSeq: null, sourceRevision, expected: null, agentStatus: 'idle',
        },
      }),
      signal: controller.signal,
    }))
    controller.abort()
    expect(await (await call)?.json()).toMatchObject({
      result: { ok: true, value: { result: { kind: 'fault', code: 'session_unavailable' } } },
    })
    await cleanup?.()
    expect(capture.disposed()).toBe(1)
  })
})
