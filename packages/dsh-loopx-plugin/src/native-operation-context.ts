/** Optional read-only native DSH metadata producer; not a Core actor issuer. */

export const name = 'loopx-native-operation-context'
export const inject = ['agents', 'tools', 'sessionProjections']
export const NATIVE_CONTEXT_TOOL = 'loopx_operation_context'

interface NativeAgent {
  id: string
  session: { id: string }
  status: string
  options: { provider?: unknown, model?: unknown, reasoningEffort?: unknown }
}

export interface Execution {
  agent?: NativeAgent
  signal: AbortSignal
  callId?: string
}

interface NativeContext {
  schema_version: 'loopx_dsh_native_context_v0'
  session_id: string
  native_turn_number: number
  turn_start_seq: number
  profile_source: 'agent_options'
  profile: { provider: string | null, model: string | null, reasoning_effort: string | null }
  wire_profile_verified: false
  execution_allowed: false
}

export interface NativeContextTool {
  name: string
  description: string
  parameters: object
  output: { schema: object, render(args: unknown, value: unknown): { type: 'text', text: string }[] }
  execute(args: unknown, exec: Execution): Promise<NativeContext>
}

/** Structural ports of DSH's existing services; this module owns no registry. */
export interface NativeContextHost {
  agents: { get(id: string): object | undefined }
  tools: {
    register(tool: object): unknown
    get(name: string, scope: object): object | undefined
  }
  sessionProjections: {
    stateOf(session: object, key: 'turnBoundary'): {
      lastTurn: number
      openTurnStartSeq: number | null
    } | undefined
  }
}

const nullableString = { oneOf: [{ type: 'string' }, { type: 'null' }] }

function observedString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

/** Reads DSH-owned services, never caller-supplied identity. Used by both the
 * read-only observation tool and the explicit managed-operation transport. */
export function readNativeContext(host: NativeContextHost, tool: { name: string }, exec: Execution): NativeContext {
  if (exec.signal.aborted) throw new Error('native context call aborted')
  const agent = exec.agent
  if (!agent || typeof agent.id !== 'string' || !agent.id
    || agent.session?.id !== agent.id || agent.status !== 'running'
    || host.agents.get(agent.id) !== agent
    || host.tools.get(tool.name, agent) !== tool) {
    throw new Error('native context requires the current live calling agent and tool')
  }
  const boundary = host.sessionProjections.stateOf(agent.session, 'turnBoundary')
  if (!boundary || !Number.isSafeInteger(boundary.lastTurn) || boundary.lastTurn < 1
    || !Number.isSafeInteger(boundary.openTurnStartSeq) || (boundary.openTurnStartSeq ?? -1) < 0) {
    throw new Error('native context requires an open native Turn')
  }
  return {
    schema_version: 'loopx_dsh_native_context_v0', session_id: agent.id,
    native_turn_number: boundary.lastTurn, turn_start_seq: boundary.openTurnStartSeq as number,
    profile_source: 'agent_options',
    profile: {
      provider: observedString(agent.options?.provider), model: observedString(agent.options?.model),
      reasoning_effort: observedString(agent.options?.reasoningEffort),
    },
    wire_profile_verified: false, execution_allowed: false,
  }
}

export function createNativeContextTool(host: NativeContextHost): NativeContextTool {
  const tool: NativeContextTool = {
    name: NATIVE_CONTEXT_TOOL,
    description: 'Read this calling DSH session, open native Turn and agent profile options. Observation only: no approval, execution grant or account access. Takes no arguments.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
    output: {
      schema: {
        type: 'object', additionalProperties: false,
        required: ['schema_version', 'session_id', 'native_turn_number', 'turn_start_seq', 'profile_source', 'profile', 'wire_profile_verified', 'execution_allowed'],
        properties: {
          schema_version: { type: 'string', const: 'loopx_dsh_native_context_v0' },
          // DSH's supported schema subset omits range/length keywords;
          // execute enforces nonempty identity and safe native boundaries.
          session_id: { type: 'string' },
          native_turn_number: { type: 'integer' },
          turn_start_seq: { type: 'integer' },
          profile_source: { type: 'string', const: 'agent_options' },
          profile: {
            type: 'object', additionalProperties: false,
            required: ['provider', 'model', 'reasoning_effort'],
            properties: { provider: nullableString, model: nullableString, reasoning_effort: nullableString },
          },
          wire_profile_verified: { type: 'boolean', const: false },
          execution_allowed: { type: 'boolean', const: false },
        },
      },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(args, exec) {
      if (args === null || typeof args !== 'object' || Array.isArray(args) || Object.keys(args).length !== 0) {
        throw new Error('native context takes no caller identity or arguments')
      }
      return readNativeContext(host, tool, exec)
    },
  }
  return tool
}

/** Explicit Loader opt-in only; the default plugin tree does not load this. */
export function apply(host: NativeContextHost): void {
  host.tools.register(createNativeContextTool(host))
}
