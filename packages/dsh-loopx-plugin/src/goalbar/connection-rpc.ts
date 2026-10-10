import type {
  ConnectionRpcHandler,
  HostConnectionHandle,
} from '@deepseek-ai/dsh-client-connection'
import {
  decodeGoalBarRequestV1,
  endpointForGoalBarOp,
} from './protocol.ts'
import type {
  GoalBarRequestV1,
  GoalBarResponseV1,
} from './protocol.ts'
import {
  fixedGoalBarFailureResponseV1,
} from './service.ts'
import type { GoalBarServiceHandle } from './service.ts'

export const GOALBAR_RPC_CHANNEL = '/loopx' as const
export const GOALBAR_SHARED_API_CHANNEL = '/api' as const
const GOALBAR_SHARED_API_ENDPOINT = 'loopx.goalbar' as const

type ConnectionRpcResult = Awaited<ReturnType<ConnectionRpcHandler>>
// The business adapter consumes no Peer identity. Connection owns admission;
// its 0.2 RPC carrier may supply a fourth Peer argument to this same callback.
type GoalBarConnectionHandler = (
  endpoint: string, payload: unknown, signal: AbortSignal,
) => ReturnType<ConnectionRpcHandler>

function badRequestCarrier(): ConnectionRpcResult {
  return {
    ok: false,
    error: {
      code: 'bad-request',
      message: 'invalid LoopX GoalBar request',
      details: { issues: [] },
    },
  }
}

function successCarrier(value: GoalBarResponseV1): ConnectionRpcResult {
  return { ok: true, value }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function responseEnvelope(rpcId: string, result: ConnectionRpcResult): Response {
  return Response.json({ type: 'server-response', rpcId, result })
}

function invalidEnvelope(rpcId = 'invalid-request'): Response {
  return responseEnvelope(rpcId, {
    ok: false,
    error: {
      code: 'bad-request',
      message: 'invalid client-request message',
      details: { issues: [] },
    },
  })
}

async function handleSharedApiRequest(
  request: Request,
  handler: GoalBarConnectionHandler,
): Promise<Response> {
  if (request.headers.get('content-type')?.split(';', 1)[0]?.trim().toLowerCase()
    !== 'application/json') {
    return new Response('content type must be application/json', { status: 415 })
  }
  let body: unknown
  try {
    body = await request.json()
  } catch {
    return new Response('body is not JSON', { status: 400 })
  }
  const rpcId = isRecord(body) && typeof body.rpcId === 'string'
    ? body.rpcId
    : 'invalid-request'
  if (!isRecord(body)
    || body.type !== 'client-request'
    || typeof body.rpcId !== 'string'
    || body.method !== GOALBAR_SHARED_API_ENDPOINT
    || !Object.hasOwn(body, 'payload')) {
    return invalidEnvelope(rpcId)
  }
  const op = isRecord(body.payload) ? body.payload.op : undefined
  if (op !== 'read' && op !== 'watch' && op !== 'start' && op !== 'pause') {
    return responseEnvelope(rpcId, badRequestCarrier())
  }
  const endpoint = endpointForGoalBarOp(op)
  try {
    return responseEnvelope(
      rpcId,
      await handler(endpoint, body.payload, request.signal),
    )
  } catch {
    return responseEnvelope(rpcId, {
      ok: false,
      error: {
        code: 'internal',
        message: 'GoalBar handler failed',
        details: {},
      },
    })
  }
}

/**
 * Close the generic Connection carrier around the GoalBar V2 business union.
 * No exception value or request payload is ever rendered into the carrier.
 */
export function createGoalBarConnectionHandler(
  service: GoalBarServiceHandle,
): GoalBarConnectionHandler {
  return async (endpoint, payload, signal) => {
    let request: GoalBarRequestV1 | undefined
    try {
      request = decodeGoalBarRequestV1(endpoint, payload)
      if (request === undefined) return badRequestCarrier()
      try {
        return successCarrier(await service.handle(request, signal))
      } catch {
        return successCarrier(fixedGoalBarFailureResponseV1(request))
      }
    } catch {
      return request === undefined
        ? badRequestCarrier()
        : successCarrier(fixedGoalBarFailureResponseV1(request))
    }
  }
}

/** @deprecated Explicit legacy registration; the plugin uses the shared API. */
export function registerGoalBarConnectionRpc(
  connection: Pick<HostConnectionHandle, 'rpc'>,
  service: GoalBarServiceHandle,
): () => Promise<void> {
  return connection.rpc.handle(GOALBAR_RPC_CHANNEL, createGoalBarConnectionHandler(service))
}

/** Register on the authenticated shared API required by the DSH peer floor. */
export function registerGoalBarConnectionTransport(
  connection: Pick<HostConnectionHandle, 'fetch'>,
  service: GoalBarServiceHandle,
): () => Promise<void> {
  const handler = createGoalBarConnectionHandler(service)
  return connection.fetch.register({
    path: `${GOALBAR_SHARED_API_CHANNEL}/${GOALBAR_SHARED_API_ENDPOINT}`,
    methods: ['POST'],
    requestBody: 'buffered',
    fetch: request => handleSharedApiRequest(request, handler),
  })
}
