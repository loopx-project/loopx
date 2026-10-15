/** Explicit SDK-owned transport. No default registration, bearer or actor API. */
import { connect } from 'node:net'
import { isAbsolute } from 'node:path'
import { readNativeContext, type Execution, type NativeContextHost } from './native-operation-context.js'

export const name = 'loopx-native-operation'
export const inject = ['agents', 'tools', 'sessionProjections']
export const OPERATION_TOOL = 'loopx_operation'
const LIMIT = 262144

export interface OperationConnectionConfig {
  socketPath: string
}

function exchange(path: string, envelope: object, signal: AbortSignal): Promise<object> {
  const frame = Buffer.from(JSON.stringify(envelope) + '\n')
  if (frame.length > LIMIT) return Promise.reject(new Error('operation frame exceeds limit'))
  return new Promise((resolve, reject) => {
    const socket = connect({ path })
    let data = Buffer.alloc(0)
    let done = false
    const finish = (error?: Error, value?: object) => {
      if (done) return
      done = true
      signal.removeEventListener('abort', aborted)
      socket.destroy()
      if (error) reject(error)
      else resolve(value!)
    }
    const aborted = () => finish(new Error('operation connection aborted; reconcile consumed results'))
    signal.addEventListener('abort', aborted, { once: true })
    if (signal.aborted) return aborted()
    socket.setTimeout(20000, () => finish(new Error('operation connection timed out; do not retry an effect')))
    socket.on('connect', () => socket.write(frame))
    socket.on('error', () => finish(new Error('operation connection unavailable; reconcile before retry')))
    socket.on('end', () => { if (!done) finish(new Error('operation connection closed without receipt')) })
    socket.on('data', chunk => {
      data = Buffer.concat([data, chunk])
      if (data.length > LIMIT) return finish(new Error('operation response exceeds limit'))
      const end = data.indexOf(10)
      if (end < 0) return
      try {
        if (end !== data.length - 1) throw new Error('extra frame')
        const value: unknown = JSON.parse(data.subarray(0, end).toString('utf8'))
        if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('invalid receipt')
        finish(undefined, value)
      } catch { finish(new Error('invalid operation response; reconcile before retry')) }
    })
  })
}

export function createNativeOperationTool(host: NativeContextHost, config: OperationConnectionConfig) {
  if (typeof config.socketPath !== 'string' || !isAbsolute(config.socketPath)) {
    throw new Error('an explicit parent-owned absolute operation socket is required')
  }
  const tool = {
    name: OPERATION_TOOL,
    description: 'Read context/pending, prepare an exact proposal, inspect, consume human approval once, or report original evidence. Identity comes from the owned native connection. A repeated consume never grants execution. Do not retry unknown effects.',
    parameters: {
      type: 'object', additionalProperties: false, required: ['action'],
      properties: {
        action: { type: 'string', enum: ['context', 'pending', 'prepare', 'inspect', 'consume', 'report'] },
        proposal_id: { type: 'string' }, consumption_id: { type: 'string' }, cursor: { type: 'string' },
        request: { type: 'object' }, outcome: { type: 'object' },
      },
    },
    output: {
      schema: { type: 'object' },
      render: (_args: unknown, value: unknown) => [{ type: 'text' as const, text: JSON.stringify(value) }],
    },
    async execute(args: unknown, exec: Execution) {
      const native = readNativeContext(host, tool, exec)
      if (!args || typeof args !== 'object' || Array.isArray(args)
        || typeof exec.callId !== 'string' || !exec.callId || exec.callId.length > 256) {
        throw new Error('operation requires native call identity and object arguments')
      }
      // No retries: a lost reply may follow a committed canonical consumption.
      return exchange(config.socketPath, {
        schema_version: 'loopx_dsh_operation_call_v0', native, call_id: exec.callId, arguments: args,
      }, exec.signal)
    },
  }
  return tool
}

export function apply(host: NativeContextHost, config: OperationConnectionConfig): void {
  host.tools.register(createNativeOperationTool(host, config))
}
