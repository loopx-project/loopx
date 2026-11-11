import type { Translate } from '@deepseek-ai/dsh-client-ui-slots'

import type { GoalBarAdmissionFailureCategoryV1 } from '../goalbar/protocol.ts'
import type { GoalBarUiErrorCode } from './useGoalBar.ts'

export const GOALBAR_LOCALE_NAMESPACE = 'loopx.goalbar' as const

const en = {
  'region.label': 'LoopX Goal status',
  'goal.label': 'Goal {goalId}',
  'status.active': 'Active',
  'status.running': 'Running',
  'status.paused': 'Paused',
  'action.start': 'Start',
  'action.pause': 'Pause',
  'action.refresh': 'Refresh',
  'action.refreshing': 'Refreshing',
  'progress.label': 'Agent-lane todos: {processed} of {total} processed',
  'progress.empty': 'No agent-lane todos',
  'continuation.blocked': 'Automatic continuation is not admitted.',
  'continuation.category.cli_unavailable': 'The LoopX CLI is unavailable.',
  'continuation.category.cli_unreachable': 'The LoopX CLI could not be reached.',
  'continuation.category.admission_rejected': 'LoopX rejected the continuation request.',
  'continuation.category.invalid_response': 'LoopX returned an incompatible response.',
  'continuation.category.driver_error': 'The continuation driver failed unexpectedly.',
  'continuation.recovery': 'Pause, then Start, to retry automatic continuation.',
  'error.session_unavailable': 'The session is unavailable. Refresh to retry.',
  'error.cli_unavailable': 'LoopX is unavailable. Refresh to retry.',
  'error.binding_read_failed': 'The LoopX binding could not be read. Refresh to retry.',
  'error.activation_read_failed': 'Goal status could not be read. Refresh to retry.',
  'error.todo_read_failed': 'Todo progress could not be read. Refresh to retry.',
  'error.protocol_mismatch': 'LoopX returned an unsupported status. Refresh to retry.',
  'error.transport_error': 'LoopX is temporarily unreachable. Refresh to retry.',
  'error.protocol_error': 'LoopX returned an unsupported response. Refresh to retry.',
  'error.binding_mismatch': 'The LoopX binding changed. Refresh before trying again.',
  'error.binding_validation_failed': 'The LoopX binding could not be verified. Refresh before trying again.',
  'error.not_actionable': 'That action is no longer available. Refresh to continue.',
  'error.action_in_flight': 'Another LoopX action is in progress. Refresh to check its result.',
  'error.operation_result_unknown': 'The action result is uncertain. Refresh before trying again.',
  'error.driver_sync_failed': 'LoopX changed, but the session did not sync. Refresh to check.',
  'error.post_read_failed': 'LoopX changed, but the latest status is unavailable. Refresh to check.',
} as const

export type GoalBarLocaleKey = keyof typeof en

const zh: Record<GoalBarLocaleKey, string> = {
  'region.label': 'LoopX 目标状态',
  'goal.label': '目标 {goalId}',
  'status.active': '已激活',
  'status.running': '运行中',
  'status.paused': '已暂停',
  'action.start': '启动',
  'action.pause': '暂停',
  'action.refresh': '刷新',
  'action.refreshing': '刷新中',
  'progress.label': 'Agent 通道待办：已处理 {processed} / {total}',
  'progress.empty': '没有 Agent 通道待办',
  'continuation.blocked': '自动续跑未被准入。',
  'continuation.category.cli_unavailable': 'LoopX CLI 不可用。',
  'continuation.category.cli_unreachable': '无法连接 LoopX CLI。',
  'continuation.category.admission_rejected': 'LoopX 拒绝了续跑请求。',
  'continuation.category.invalid_response': 'LoopX 返回了不兼容的响应。',
  'continuation.category.driver_error': '续跑驱动意外失败。',
  'continuation.recovery': '请先暂停再启动，以重试自动续跑。',
  'error.session_unavailable': '会话不可用。请刷新后重试。',
  'error.cli_unavailable': 'LoopX 当前不可用。请刷新后重试。',
  'error.binding_read_failed': '无法读取 LoopX 绑定。请刷新后重试。',
  'error.activation_read_failed': '无法读取目标状态。请刷新后重试。',
  'error.todo_read_failed': '无法读取待办进度。请刷新后重试。',
  'error.protocol_mismatch': 'LoopX 返回了不支持的状态。请刷新后重试。',
  'error.transport_error': '暂时无法连接 LoopX。请刷新后重试。',
  'error.protocol_error': 'LoopX 返回了不支持的响应。请刷新后重试。',
  'error.binding_mismatch': 'LoopX 绑定已变化。请刷新后再试。',
  'error.binding_validation_failed': '无法验证 LoopX 绑定。请刷新后再试。',
  'error.not_actionable': '此操作已不可用。请刷新后继续。',
  'error.action_in_flight': '另一个 LoopX 操作正在进行。请刷新查看结果。',
  'error.operation_result_unknown': '操作结果不确定。请刷新后再试。',
  'error.driver_sync_failed': 'LoopX 已更新，但会话同步失败。请刷新检查。',
  'error.post_read_failed': 'LoopX 已更新，但最新状态不可用。请刷新检查。',
}

export const GOALBAR_LOCALES = Object.freeze({ en, zh })

/** Dictionary namespace of the LoopX personal-workspace sidebar panel. */
export const DASHBOARD_LOCALE_NAMESPACE = 'loopx.dashboard' as const

const dashboardEn = {
  'iframe.title': 'LoopX personal workspace',
  'panel.unavailable': 'The LoopX dashboard is not reachable yet. Run `loopx dashboard` first.',
  'panel.retry': 'Retry',
} as const

export type DashboardLocaleKey = keyof typeof dashboardEn

const dashboardZh: Record<DashboardLocaleKey, string> = {
  'iframe.title': 'LoopX 个人工作区',
  'panel.unavailable': '尚未连接到 LoopX 本地看板。请先在终端运行 `loopx dashboard`。',
  'panel.retry': '重试',
}

export const DASHBOARD_LOCALES = Object.freeze({ en: dashboardEn, zh: dashboardZh })

declare module '@deepseek-ai/dsh-client-ui-slots' {
  interface LocaleNamespaceMap {
    'loopx.goalbar': GoalBarLocaleKey
    'loopx.dashboard': DashboardLocaleKey
  }
}

export type GoalBarTranslate = Translate<GoalBarLocaleKey>

export type DashboardTranslate = Translate<DashboardLocaleKey>

const ERROR_KEYS: Readonly<Record<GoalBarUiErrorCode, GoalBarLocaleKey>> = {
  session_unavailable: 'error.session_unavailable',
  cli_unavailable: 'error.cli_unavailable',
  binding_read_failed: 'error.binding_read_failed',
  activation_read_failed: 'error.activation_read_failed',
  todo_read_failed: 'error.todo_read_failed',
  protocol_mismatch: 'error.protocol_mismatch',
  transport_error: 'error.transport_error',
  protocol_error: 'error.protocol_error',
  binding_mismatch: 'error.binding_mismatch',
  binding_validation_failed: 'error.binding_validation_failed',
  not_actionable: 'error.not_actionable',
  action_in_flight: 'error.action_in_flight',
  operation_result_unknown: 'error.operation_result_unknown',
  driver_sync_failed: 'error.driver_sync_failed',
  post_read_failed: 'error.post_read_failed',
}

export function goalBarErrorKey(code: GoalBarUiErrorCode): GoalBarLocaleKey {
  return ERROR_KEYS[code]
}

const ADMISSION_CATEGORY_KEYS: Readonly<
  Record<GoalBarAdmissionFailureCategoryV1, GoalBarLocaleKey>
> = {
  cli_unavailable: 'continuation.category.cli_unavailable',
  cli_unreachable: 'continuation.category.cli_unreachable',
  admission_rejected: 'continuation.category.admission_rejected',
  invalid_response: 'continuation.category.invalid_response',
  driver_error: 'continuation.category.driver_error',
}

export function goalBarAdmissionCategoryKey(
  category: GoalBarAdmissionFailureCategoryV1,
): GoalBarLocaleKey {
  return ADMISSION_CATEGORY_KEYS[category]
}
