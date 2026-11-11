import type { JSX } from 'react'

import type { GoalBarSnapshotV1 } from '../goalbar/protocol.ts'
import styles from './goalbar.module.css'
import {
  goalBarAdmissionCategoryKey,
  goalBarErrorKey,
  type GoalBarTranslate,
} from './locale.ts'
import type { GoalBarRpc } from './rpc.ts'
import {
  useGoalBar,
  type GoalBarConnectionResetSubscriber,
} from './useGoalBar.ts'

export interface LoopXGoalBarProps {
  /** Exact identity supplied only by conversation.input.dock inject(sessionId). */
  readonly rpcSessionId: string
  readonly rpc: GoalBarRpc
  readonly t: GoalBarTranslate
  readonly subscribeConnectionReset?: GoalBarConnectionResetSubscriber | undefined
}

function visibleStatus(
  snapshot: GoalBarSnapshotV1,
): 'active' | 'running' | 'paused' {
  if (snapshot.goalActivation === 'stopped') return 'paused'
  return snapshot.agentStatus === 'running' ? 'running' : 'active'
}

function classes(...values: Array<string | undefined | false>): string {
  return values.filter((value): value is string => Boolean(value)).join(' ')
}

/** Compact, accessible presentation for one exact LoopX Goal binding. */
export function LoopXGoalBar({
  rpcSessionId,
  rpc,
  t,
  subscribeConnectionReset,
}: LoopXGoalBarProps): JSX.Element | null {
  const goalBar = useGoalBar({
    rpcSessionId,
    rpc,
    subscribeConnectionReset,
  })
  const snapshot = goalBar.snapshot
  if (snapshot === null) return null

  const status = visibleStatus(snapshot)
  const admission = snapshot.continuationAdmission
  const admissionBlocked = admission.state === 'failed'
  const empty = snapshot.progress.total === 0
  const progressLabel = empty
    ? t('progress.empty')
    : t('progress.label', {
        processed: snapshot.progress.processed,
        total: snapshot.progress.total,
      })
  const actionLabel = goalBar.action === 'start'
    ? t('action.start')
    : t('action.pause')

  return (
    <div className={styles.dock}>
      <section
        className={classes(styles.panel, goalBar.errorCode !== null && styles.stale)}
        aria-label={t('region.label')}
        aria-busy={goalBar.syncing || goalBar.pendingAction}
      >
        <div className={styles.row}>
          <span className={styles.brand}>LoopX</span>
          <span
            className={styles.goalId}
            aria-label={t('goal.label', { goalId: snapshot.goalId })}
            title={snapshot.goalId}
          >
            {snapshot.goalId}
          </span>
          <progress
            className={styles.progress}
            max={empty ? 1 : snapshot.progress.total}
            value={snapshot.progress.processed}
            aria-label={progressLabel}
          />
          <span className={styles.progressText} aria-hidden="true">
            {snapshot.progress.processed} / {snapshot.progress.total}
          </span>
          <span className={styles.status} data-status={status}>
            <span className={styles.statusMark} aria-hidden="true" />
            {t(`status.${status}`)}
          </span>
          <button
            type="button"
            className={styles.action}
            disabled={goalBar.actionDisabled}
            onClick={() => {
              if (goalBar.action !== null) goalBar.requestAction(goalBar.action)
            }}
          >
            {actionLabel}
          </button>
          {goalBar.errorCode !== null && (
            <button
              type="button"
              className={styles.refresh}
              disabled={goalBar.syncing || goalBar.pendingAction}
              onClick={goalBar.refresh}
            >
              {goalBar.syncing
                ? t('action.refreshing')
                : t('action.refresh')}
            </button>
          )}
        </div>
        {admissionBlocked && (
          <p className={styles.error} role="status">
            <span aria-hidden="true">!</span>
            <span>
              {t('continuation.blocked')}
              {' '}
              {t(goalBarAdmissionCategoryKey(admission.category))}
              {' '}
              {t('continuation.recovery')}
            </span>
          </p>
        )}
        {goalBar.errorCode !== null && (
          <p className={styles.error} role="status">
            <span aria-hidden="true">!</span>
            <span>{t(goalBarErrorKey(goalBar.errorCode))}</span>
          </p>
        )}
      </section>
    </div>
  )
}
