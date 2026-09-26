import {
  LoopXCliError,
  runFile,
  runJsonCommand,
} from '../cli.ts'
import type {
  FileRunner,
  LoopXCommand,
} from '../cli.ts'
import {
  isGoalBarAgentId,
  isGoalBarGoalId,
  isGoalBarSessionId,
} from './protocol.ts'
import type {
  GoalBarActivationV1,
  GoalBarAgentStatusV1,
  GoalBarProgressV1,
  GoalBarReadFaultCode,
  GoalBarSnapshotV1,
} from './protocol.ts'
import {
  decodeGoalBarProjectRegistry,
  ProjectRegistryWireError,
} from './project-registry-wire.ts'

export const GOALBAR_HOST_SURFACE = 'deepseek-harness-native' as const
export const GOALBAR_PROJECT_REGISTRY = '.loopx/registry.json' as const
export const GOALBAR_ACTIVE_STATE_ROOT = '.loopx/goals' as const
export const GOALBAR_ACTIVE_STATE_FILE = 'ACTIVE_GOAL_STATE.md' as const

const SOURCE_REVISION_FAILURE = `sha256:${createHash('sha256')
  .update('loopx-goalbar-source-revision-unavailable-v1')
  .digest('hex')}`

const BINDING_SCHEMA = 'loopx_thread_agent_binding_resolution_v0'
const ACTIVATION_TRANSITION_SCHEMA = 'loopx_goal_activation_transition_v1'
const ACTIVATION_READBACK_SCHEMA = 'loopx_goal_activation_readback_v1'
const TODO_PROJECTION_SCHEMA = 'agent_lane_todo_list_projection_v0'
const TODO_PROJECTION_VIEW = 'explicit_limit_cold_path'
const READ_ATTEMPTS = 3

export interface GoalBarCliReadOptions {
  readonly command: LoopXCommand
  readonly cwd: string
  readonly runner?: FileRunner | undefined
  readonly signal?: AbortSignal | undefined
  readonly env?: NodeJS.ProcessEnv | undefined
  readonly retryDelaysMs?: readonly number[] | undefined
}

export interface GoalBarReadModelOptions extends GoalBarCliReadOptions {
  readonly sessionId: string
  readonly agentStatus: GoalBarAgentStatusV1
}

export interface GoalBarSourceRevisionOptions {
  readonly cwd: string
  readonly goalId?: string | undefined
  readonly loopxAgentId?: string | undefined
}

export class GoalBarSourceRevisionError extends Error {}

export function unavailableGoalBarSourceRevision(): string {
  return SOURCE_REVISION_FAILURE
}

function frame(hash: ReturnType<typeof createHash>, label: string, value: Buffer): void {
  const labelBytes = Buffer.from(label, 'utf8')
  const lengths = Buffer.allocUnsafe(8)
  lengths.writeUInt32BE(labelBytes.length, 0)
  lengths.writeUInt32BE(value.length, 4)
  hash.update(lengths)
  hash.update(labelBytes)
  hash.update(value)
}

function isContained(root: string, candidate: string): boolean {
  const pathFromRoot = relative(root, candidate)
  return pathFromRoot === ''
    || (!pathFromRoot.startsWith(`..${sep}`)
      && pathFromRoot !== '..'
      && !isAbsolute(pathFromRoot))
}

function validatedSourcePaths(options: GoalBarSourceRevisionOptions): string[] {
  if (!isAbsolute(options.cwd) || resolve(options.cwd) !== options.cwd) {
    throw new GoalBarSourceRevisionError('project cwd must be absolute and normalized')
  }
  const paths = [resolve(options.cwd, GOALBAR_PROJECT_REGISTRY)]
  if (options.goalId !== undefined) {
    if (!isGoalBarGoalId(options.goalId)
      || options.goalId === '.'
      || options.goalId === '..'
      || options.goalId.includes('/')
      || options.goalId.includes('\\')) {
      throw new GoalBarSourceRevisionError('goal id is not a safe path segment')
    }
  }
  if ((options.goalId === undefined) !== (options.loopxAgentId === undefined)
    || (options.loopxAgentId !== undefined
      && !isGoalBarAgentId(options.loopxAgentId))) {
    throw new GoalBarSourceRevisionError('source revision requires an exact binding pair')
  }
  if (paths.some(candidate => !isContained(options.cwd, candidate))) {
    throw new GoalBarSourceRevisionError('source path escaped project cwd')
  }
  return paths
}

function registeredActiveStatePath(cwd: string, goalId: string, registry: Buffer): string {
  let payload: unknown
  try {
    payload = decodeGoalBarProjectRegistry(registry)
  } catch (error: unknown) {
    if (error instanceof ProjectRegistryWireError) {
      throw new GoalBarSourceRevisionError(error.message)
    }
    throw new GoalBarSourceRevisionError('project registry is invalid')
  }
  if (typeof payload !== 'object' || payload === null
    || !('goals' in payload) || !Array.isArray(payload.goals)) {
    throw new GoalBarSourceRevisionError('project registry has no Goal list')
  }
  const selected = payload.goals.find((goal: unknown) =>
    typeof goal === 'object' && goal !== null && 'id' in goal && goal.id === goalId)
  let declared = join(GOALBAR_ACTIVE_STATE_ROOT, goalId, GOALBAR_ACTIVE_STATE_FILE)
  if (selected !== undefined) {
    if (!('state_file' in selected)
      || typeof selected.state_file !== 'string'
      || !selected.state_file.trim()) {
      throw new GoalBarSourceRevisionError('registered Goal has no state_file')
    }
    declared = selected.state_file
  }
  const path = resolve(cwd, declared)
  if (!isContained(cwd, path)) {
    throw new GoalBarSourceRevisionError('registered Goal state path escaped project cwd')
  }
  return path
}

async function readRevisionSource(
  cwd: string,
  path: string,
): Promise<{ readonly exists: boolean; readonly content: Buffer }> {
  try {
    const resolved = await realpath(path)
    const resolvedRoot = await realpath(cwd)
    if (!isContained(resolvedRoot, resolved)) {
      throw new GoalBarSourceRevisionError('source resolved outside project cwd')
    }
    return { exists: true, content: await readFile(resolved) }
  } catch (error: unknown) {
    if ((error as NodeJS.ErrnoException)?.code === 'ENOENT') {
      return { exists: false, content: Buffer.alloc(0) }
    }
    throw error instanceof GoalBarSourceRevisionError
      ? error
      : new GoalBarSourceRevisionError('authoritative source read failed')
  }
}

/** Hash the registry-declared active state; contents and local paths never cross the wire. */
export async function computeGoalBarSourceRevision(
  options: GoalBarSourceRevisionOptions,
): Promise<string> {
  const paths = validatedSourcePaths(options)
  const hash = createHash('sha256')
  frame(hash, 'contract', Buffer.from('loopx-goalbar-source-revision-v2'))
  if (options.goalId !== undefined && options.loopxAgentId !== undefined) {
    frame(hash, 'binding.goal-id', Buffer.from(options.goalId, 'utf8'))
    frame(hash, 'binding.agent-id', Buffer.from(options.loopxAgentId, 'utf8'))
  }
  const registry = await readRevisionSource(options.cwd, paths[0] as string)
  frame(hash, 'registry.exists', Buffer.from(registry.exists ? '1' : '0'))
  frame(hash, 'registry.content', registry.content)
  if (options.goalId !== undefined) {
    const statePath = registry.exists
      ? registeredActiveStatePath(options.cwd, options.goalId, registry.content)
      : resolve(options.cwd, GOALBAR_ACTIVE_STATE_ROOT, options.goalId, GOALBAR_ACTIVE_STATE_FILE)
    const source = await readRevisionSource(options.cwd, statePath)
    frame(hash, 'active-state.exists', Buffer.from(source.exists ? '1' : '0'))
    frame(hash, 'active-state.content', source.content)
  }
  return `sha256:${hash.digest('hex')}`
}

export interface GoalBarStableReadResult {
  readonly model: GoalBarReadModelResult
  readonly sourceRevision: string
}

export type GoalBarSourceRevisionReader = (
  options: GoalBarSourceRevisionOptions,
) => Promise<string>

/**
 * Observe the same authoritative bytes before and after their CLI-derived read.
 * A concurrent equal-size write or atomic replacement retries the whole model.
 */
export async function readStableGoalBarModel(
  options: GoalBarReadModelOptions,
  revisionReader: GoalBarSourceRevisionReader = computeGoalBarSourceRevision,
): Promise<GoalBarStableReadResult> {
  let lastRevision = await revisionReader({ cwd: options.cwd })
  for (let attempt = 0; attempt < READ_ATTEMPTS; attempt += 1) {
    const registryBefore = attempt === 0
      ? lastRevision
      : await revisionReader({ cwd: options.cwd })
    const binding = await readGoalBarBinding(options, options.sessionId)
    const registryAfter = await revisionReader({ cwd: options.cwd })
    lastRevision = registryAfter
    if (registryBefore !== registryAfter) continue
    if (binding.kind === 'fault') return { model: binding, sourceRevision: registryAfter }
    if (binding.kind === 'missing') {
      return {
        model: { kind: 'hidden', reason: 'binding_missing' },
        sourceRevision: registryAfter,
      }
    }
    if (binding.kind === 'ambiguous') {
      return {
        model: {
          kind: 'hidden',
          reason: 'binding_ambiguous',
          uniquePairCount: binding.uniquePairCount,
        },
        sourceRevision: registryAfter,
      }
    }
    if (binding.kind === 'unavailable') {
      return {
        model: { kind: 'fault', code: 'binding_read_failed' },
        sourceRevision: registryAfter,
      }
    }

    const exact = { goalId: binding.goalId, loopxAgentId: binding.loopxAgentId }
    const sourceBefore = await revisionReader({
      cwd: options.cwd,
      ...exact,
    })
    const registryGuard = await revisionReader({ cwd: options.cwd })
    if (registryGuard !== registryAfter) {
      lastRevision = registryGuard
      continue
    }
    const [activation, progress] = await Promise.all([
      readGoalBarActivation(options, binding.goalId),
      readGoalBarProgress(options, binding.goalId, binding.loopxAgentId),
    ])
    const sourceAfter = await revisionReader({
      cwd: options.cwd,
      ...exact,
    })
    lastRevision = sourceAfter
    if (sourceBefore !== sourceAfter) continue
    const fault = preferredReadFault(activation, progress)
    if (fault !== undefined) {
      return { model: { ...fault, binding }, sourceRevision: sourceAfter }
    }
    if (activation.kind !== 'value' || progress.kind !== 'value') {
      return {
        model: { kind: 'fault', code: 'protocol_mismatch', binding },
        sourceRevision: sourceAfter,
      }
    }
    return {
      model: {
        kind: 'present',
        snapshot: {
          sessionId: options.sessionId,
          ...exact,
          goalActivation: activation.goalActivation,
          agentStatus: options.agentStatus,
          progress: progress.progress,
        },
      },
      sourceRevision: sourceAfter,
    }
  }
  return {
    model: { kind: 'fault', code: 'binding_read_failed' },
    sourceRevision: lastRevision,
  }
}

export type DecodedBindingResolutionV0 =
  | { readonly kind: 'missing' }
  | {
      readonly kind: 'bound'
      readonly goalId: string
      readonly loopxAgentId: string
    }
  | { readonly kind: 'ambiguous'; readonly uniquePairCount: number }
  | { readonly kind: 'unavailable' }

export type GoalBarBindingReadResult =
  | DecodedBindingResolutionV0
  | { readonly kind: 'fault'; readonly code: GoalBarReadFaultCode }

export type GoalBarActivationReadResult =
  | { readonly kind: 'value'; readonly goalActivation: GoalBarActivationV1 }
  | { readonly kind: 'fault'; readonly code: GoalBarReadFaultCode }

export type GoalBarProgressReadResult =
  | { readonly kind: 'value'; readonly progress: GoalBarProgressV1 }
  | { readonly kind: 'fault'; readonly code: GoalBarReadFaultCode }

export type GoalBarReadModelResult =
  | { readonly kind: 'hidden'; readonly reason: 'binding_missing' }
  | {
      readonly kind: 'hidden'
      readonly reason: 'binding_ambiguous'
      readonly uniquePairCount: number
    }
  | { readonly kind: 'present'; readonly snapshot: GoalBarSnapshotV1 }
  | {
      readonly kind: 'fault'
      readonly code: GoalBarReadFaultCode
      readonly binding?: BindingPair | undefined
    }

interface JsonReadResult {
  readonly exitCode: number
  readonly payload: Record<string, unknown>
}

export interface BindingPair {
  readonly goalId: string
  readonly loopxAgentId: string
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined
}

function safeCount(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}

function parseBindingPairs(value: unknown): BindingPair[] | undefined {
  if (!Array.isArray(value)) return undefined
  const pairs = new Map<string, BindingPair>()
  for (const item of value) {
    const candidate = record(item)
    if (!isGoalBarGoalId(candidate?.goal_id)
      || !isGoalBarAgentId(candidate.agent_id)) return undefined
    const pair = {
      goalId: candidate.goal_id,
      loopxAgentId: candidate.agent_id,
    }
    pairs.set(`${pair.goalId}\u0000${pair.loopxAgentId}`, pair)
  }
  return [...pairs.values()]
}

/**
 * Validate the authoritative resolver relation while retaining process status.
 * Duplicate records for one exact pair are collapsed before the 0/1/>1 test.
 */
export function decodeThreadAgentBindingResolutionV0(
  value: unknown,
  expectedSessionId: string,
  exitCode: number,
): DecodedBindingResolutionV0 | undefined {
  const input = record(value)
  const pairs = parseBindingPairs(input?.matches)
  if (!isGoalBarSessionId(expectedSessionId)
    || !safeCount(exitCode)
    || input?.schema_version !== BINDING_SCHEMA
    || pairs === undefined) return undefined

  if (input.status === 'missing') {
    return exitCode === 0
      && input.ok === true
      && input.host_surface === GOALBAR_HOST_SURFACE
      && input.thread_id === expectedSessionId
      && input.goal_id === null
      && input.agent_id === null
      && pairs.length === 0
      ? { kind: 'missing' }
      : undefined
  }

  if (input.status === 'bound') {
    const pair = pairs.length === 1 ? pairs[0] : undefined
    return exitCode === 0
      && input.ok === true
      && input.host_surface === GOALBAR_HOST_SURFACE
      && input.thread_id === expectedSessionId
      && pair !== undefined
      && input.goal_id === pair.goalId
      && input.agent_id === pair.loopxAgentId
      ? {
          kind: 'bound',
          goalId: pair.goalId,
          loopxAgentId: pair.loopxAgentId,
        }
      : undefined
  }

  if (input.status === 'ambiguous') {
    return exitCode !== 0
      && input.ok === false
      && input.host_surface === GOALBAR_HOST_SURFACE
      && input.thread_id === expectedSessionId
      && input.goal_id === null
      && input.agent_id === null
      && input.error_kind === 'thread_agent_binding_ambiguous'
      && pairs.length > 1
      ? { kind: 'ambiguous', uniquePairCount: pairs.length }
      : undefined
  }

  if (input.status === 'unavailable') {
    const errorKind = input.error_kind
    const exactEcho = errorKind === 'thread_agent_binding_resolution_failed'
      && input.host_surface === GOALBAR_HOST_SURFACE
      && input.thread_id === expectedSessionId
    const redactedInvalidRequest = errorKind === 'thread_agent_binding_invalid_request'
      && input.host_surface === null
      && input.thread_id === null
    return exitCode !== 0
      && input.ok === false
      && input.goal_id === null
      && input.agent_id === null
      && pairs.length === 0
      && (exactEcho || redactedInvalidRequest)
      ? { kind: 'unavailable' }
      : undefined
  }

  return undefined
}

export function decodeGoalActivationPreviewV1(
  value: unknown,
  expectedGoalId: string,
  exitCode: number,
): GoalBarActivationV1 | undefined {
  const input = record(value)
  const readback = record(input?.readback)
  if (!isGoalBarGoalId(expectedGoalId)
    || exitCode !== 0
    || input?.schema_version !== ACTIVATION_TRANSITION_SCHEMA
    || input.goal_id !== expectedGoalId
    || input.ok !== true
    || input.dry_run !== true
    || input.execute !== false
    || input.written !== false
    || input.partial_write !== false
    || input.after_state !== 'stopped'
    || readback?.schema_version !== ACTIVATION_READBACK_SCHEMA) return undefined

  if (input.before_state === 'active') {
    return input.changed === true
      && readback.status === 'not_executed'
      && readback.verified === false
      ? 'active'
      : undefined
  }
  if (input.before_state === 'stopped') {
    return input.changed === false
      && readback.status === 'not_required'
      && readback.verified === true
      ? 'stopped'
      : undefined
  }
  return undefined
}

export function decodeAgentLaneTodoProgressV0(
  value: unknown,
  expectedGoalId: string,
  expectedAgentId: string,
  exitCode: number,
): GoalBarProgressV1 | undefined {
  const input = record(value)
  const projection = record(input?.todo_list_projection)
  const agentTodos = record(input?.agent_todos)
  const items = agentTodos?.items
  const returnedItems = input?.todos
  const processed = agentTodos?.done_count
  const remaining = agentTodos?.open_count
  const total = agentTodos?.total_count
  const returnedCount = input?.returned_todo_count

  if (!isGoalBarGoalId(expectedGoalId)
    || !isGoalBarAgentId(expectedAgentId)
    || exitCode !== 0
    || input?.ok !== true
    || input.read_only !== true
    || input.command !== 'list'
    || input.goal_id !== expectedGoalId
    || input.agent_id_filter !== expectedAgentId
    || input.role !== 'agent'
    || input.explicit_limit !== 1
    || projection?.schema_version !== TODO_PROJECTION_SCHEMA
    || projection.view !== TODO_PROJECTION_VIEW
    || projection.item_limit_per_role !== 1
    || projection.counts_cover_full_match !== true
    || !safeCount(processed)
    || !safeCount(remaining)
    || !safeCount(total)
    || !Number.isSafeInteger(processed + remaining)
    || processed + remaining !== total
    || !safeCount(input.todo_count)
    || !safeCount(returnedCount)
    || !safeCount(projection.matched_todo_count)
    || !safeCount(projection.returned_todo_count)
    || total !== projection.matched_todo_count
    || input.todo_count !== returnedCount
    || returnedCount !== projection.returned_todo_count
    || !Array.isArray(items)
    || !Array.isArray(returnedItems)
    || items.length !== returnedCount
    || returnedItems.length !== returnedCount
    || returnedCount > 1) return undefined

  return { processed, remaining, total }
}

async function executeJsonRead(
  options: GoalBarCliReadOptions,
  args: readonly string[],
): Promise<JsonReadResult> {
  const runner = options.runner ?? runFile
  let exitCode: number | undefined
  const recordingRunner: FileRunner = async (file, fileArgs, runOptions) => {
    const result = await runner(file, fileArgs, runOptions)
    exitCode = result.exitCode
    return result
  }
  const payload = await runJsonCommand(options.command, args, {
    runner: recordingRunner,
    cwd: options.cwd,
    env: options.env,
    signal: options.signal,
    attempts: READ_ATTEMPTS,
    retryDelaysMs: options.retryDelaysMs,
  })
  if (exitCode === undefined) {
    throw new LoopXCliError(
      'transport',
      'LoopX command result was unavailable',
      true,
    )
  }
  return { exitCode, payload }
}

function fixedFault(
  error: unknown,
  stageCode: 'binding_read_failed' | 'activation_read_failed' | 'todo_read_failed',
): { readonly kind: 'fault'; readonly code: GoalBarReadFaultCode } {
  return {
    kind: 'fault',
    code: error instanceof LoopXCliError && error.kind === 'missing'
      ? 'cli_unavailable'
      : stageCode,
  }
}

export async function readGoalBarBinding(
  options: GoalBarCliReadOptions,
  sessionId: string,
): Promise<GoalBarBindingReadResult> {
  if (!isGoalBarSessionId(sessionId)) {
    return { kind: 'fault', code: 'protocol_mismatch' }
  }
  try {
    const result = await executeJsonRead(options, [
      '--registry', GOALBAR_PROJECT_REGISTRY,
      '--format', 'json',
      'resolve-agent-thread',
      '--host-surface', GOALBAR_HOST_SURFACE,
      '--thread-id', sessionId,
    ])
    const decoded = decodeThreadAgentBindingResolutionV0(
      result.payload,
      sessionId,
      result.exitCode,
    )
    return decoded === undefined || decoded.kind === 'unavailable'
      ? { kind: 'fault', code: 'binding_read_failed' }
      : decoded
  } catch (error: unknown) {
    return fixedFault(error, 'binding_read_failed')
  }
}

export async function readGoalBarActivation(
  options: GoalBarCliReadOptions,
  goalId: string,
): Promise<GoalBarActivationReadResult> {
  if (!isGoalBarGoalId(goalId)) {
    return { kind: 'fault', code: 'protocol_mismatch' }
  }
  try {
    const result = await executeJsonRead(options, [
      '--registry', GOALBAR_PROJECT_REGISTRY,
      '--format', 'json',
      'goal-lifecycle',
      '--goal-id', goalId,
      '--operation', 'stop',
    ])
    const goalActivation = decodeGoalActivationPreviewV1(
      result.payload,
      goalId,
      result.exitCode,
    )
    return goalActivation === undefined
      ? { kind: 'fault', code: 'activation_read_failed' }
      : { kind: 'value', goalActivation }
  } catch (error: unknown) {
    return fixedFault(error, 'activation_read_failed')
  }
}

export async function readGoalBarProgress(
  options: GoalBarCliReadOptions,
  goalId: string,
  loopxAgentId: string,
): Promise<GoalBarProgressReadResult> {
  if (!isGoalBarGoalId(goalId) || !isGoalBarAgentId(loopxAgentId)) {
    return { kind: 'fault', code: 'protocol_mismatch' }
  }
  try {
    const result = await executeJsonRead(options, [
      '--registry', GOALBAR_PROJECT_REGISTRY,
      '--format', 'json',
      'todo', 'list',
      '--goal-id', goalId,
      '--role', 'agent',
      '--agent-id', loopxAgentId,
      '--limit', '1',
    ])
    const progress = decodeAgentLaneTodoProgressV0(
      result.payload,
      goalId,
      loopxAgentId,
      result.exitCode,
    )
    return progress === undefined
      ? { kind: 'fault', code: 'todo_read_failed' }
      : { kind: 'value', progress }
  } catch (error: unknown) {
    return fixedFault(error, 'todo_read_failed')
  }
}

function preferredReadFault(
  activation: GoalBarActivationReadResult,
  progress: GoalBarProgressReadResult,
): { readonly kind: 'fault'; readonly code: GoalBarReadFaultCode } | undefined {
  const faults = [activation, progress].filter(
    (result): result is Extract<typeof result, { readonly kind: 'fault' }> => (
      result.kind === 'fault'
    ),
  )
  if (faults.length === 0) return undefined
  return faults.find(fault => fault.code === 'cli_unavailable')
    ?? faults.find(fault => fault.code === 'protocol_mismatch')
    ?? faults[0]
}

export async function readGoalBarModel(
  options: GoalBarReadModelOptions,
): Promise<GoalBarReadModelResult> {
  if (!isGoalBarSessionId(options.sessionId)
    || (options.agentStatus !== 'idle' && options.agentStatus !== 'running')) {
    return { kind: 'fault', code: 'protocol_mismatch' }
  }

  const binding = await readGoalBarBinding(options, options.sessionId)
  if (binding.kind === 'fault') return binding
  if (binding.kind === 'missing') {
    return { kind: 'hidden', reason: 'binding_missing' }
  }
  if (binding.kind === 'ambiguous') {
    return {
      kind: 'hidden',
      reason: 'binding_ambiguous',
      uniquePairCount: binding.uniquePairCount,
    }
  }
  if (binding.kind === 'unavailable') {
    return { kind: 'fault', code: 'binding_read_failed' }
  }

  const [activation, progress] = await Promise.all([
    readGoalBarActivation(options, binding.goalId),
    readGoalBarProgress(options, binding.goalId, binding.loopxAgentId),
  ])
  const fault = preferredReadFault(activation, progress)
  if (fault !== undefined) return { ...fault, binding }
  if (activation.kind !== 'value' || progress.kind !== 'value') {
    return { kind: 'fault', code: 'protocol_mismatch', binding }
  }
  return {
    kind: 'present',
    snapshot: {
      sessionId: options.sessionId,
      goalId: binding.goalId,
      loopxAgentId: binding.loopxAgentId,
      goalActivation: activation.goalActivation,
      agentStatus: options.agentStatus,
      progress: progress.progress,
    },
  }
}
import { createHash } from 'node:crypto'
import { readFile, realpath } from 'node:fs/promises'
import { isAbsolute, join, relative, resolve, sep } from 'node:path'
