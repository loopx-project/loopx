import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import {
  registerGoalBarConnectionTransport,
} from './goalbar/connection-rpc.ts'
import { goalBarCoordinator } from './goalbar/events.ts'
import { createGoalBarService } from './goalbar/service.ts'
import { resolvePluginLoopXCommand } from './managed-runtime.ts'

export const name = 'dsh-loopx-plugin'
export const inject = ['agents', 'connection', 'loopxBootstrap']

/** Package-root Host plugin: one GoalBar service, never a second Driver. */
export function apply(ctx: Context): void {
  ctx.effect(() => {
    const service = createGoalBarService({
      getAgent: sessionId => ctx.agents.get(sessionId as Agent['id']),
      coordinator: goalBarCoordinator,
      resolveCommand: signal => resolvePluginLoopXCommand({ signal }),
      warn: message => { ctx.logger.warn(message) },
    })
    const disposeRpc = registerGoalBarConnectionTransport(ctx.connection, service)
    return async () => {
      await service.dispose()
      await disposeRpc()
    }
  }, 'dsh-loopx GoalBar Host service')
}

export {
  LoopXCliError,
  resolveLoopXCommand,
  runFile,
  runJsonCommand,
  runJsonMutationCommand,
} from './cli.ts'
export type {
  FileResult,
  FileRunner,
  FileRunOptions,
  JsonCommandOptions,
  JsonMutationCommandOptions,
  LoopXCliErrorKind,
  LoopXCommand,
} from './cli.ts'
export {
  createGoalBarConnectionHandler,
  GOALBAR_RPC_CHANNEL,
  GOALBAR_SHARED_API_CHANNEL,
  registerGoalBarConnectionTransport,
  registerGoalBarConnectionRpc,
} from './goalbar/connection-rpc.ts'
export {
  createGoalBarService,
  decodeGoalBarLifecycleExecutionV1,
  GoalBarService,
} from './goalbar/service.ts'
export type {
  GoalBarCoordinatorPort,
  GoalBarServiceHandle,
  GoalBarServiceOptions,
} from './goalbar/service.ts'
export { LoopXContinuationDriver } from './driver.ts'
export type {
  DriverClock,
  LoopXDriverOptions,
  LoopXTodoFoldMessageSource,
  TodoUnitAnchor,
  TodoUnitAnchorInput,
  TodoUnitCompletionReceipt,
  TodoUnitFoldOutcome,
  TodoUnitFoldPolicy,
  TodoUnitFoldRequest,
  TodoUnitObsNextCommand,
  TodoUnitObsRunner,
  TodoUnitReceiptMu,
  TodoUnitSourceRef,
} from './driver.ts'
export {
  initializeLoopX,
  LoopXInitError,
} from './init-command.ts'
export type {
  LoopXInitOptions,
  LoopXInitStage,
  LoopXInitSummary,
} from './init-command.ts'
export { resolvePluginLoopXCommand } from './managed-runtime.ts'
export type { LoopXRuntimeOptions } from './managed-runtime.ts'
