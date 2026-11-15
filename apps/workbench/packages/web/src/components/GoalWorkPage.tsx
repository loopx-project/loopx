import { useEffect, useMemo, useState } from 'react';
import { t } from '../i18n';
import { buildSeenMarker, compareSeenMarker, readSeenMarker, seenMarkerStorage, seenMarkerStorageKey, writeSeenMarker, type SeenMarkerInput } from '../loopx/since-you-looked';
import type { LoopxAttention, LoopxGoal, LoopxStatusFailure } from '../loopx/status';

/**
 * The Goal work page's overview tab: Vitals, "since you looked", and the rows that wait on the
 * person. Batch A shows what the status projection carries; fields it does not carry say unknown
 * rather than guessing. The marker (what changed since the last visit) is local to the browser.
 */
export function GoalOverview({ goal, attention }: { goal: LoopxGoal; attention: LoopxAttention | null }) {
  const openUser = attention?.userTodos.open ?? 0;
  const openAgent = attention?.agentTodos.open ?? 0;
  const doneUser = attention ? attention.userTodos.done : null;
  const [marker] = useState(() => {
    const storage = seenMarkerStorage();
    const key = seenMarkerStorageKey('loopx', goal.goalId);
    return { storage, key, previous: readSeenMarker(storage, key) };
  });
  const input = useMemo<SeenMarkerInput>(() => ({
    observedAt: new Date().toISOString(),
    counts: { open: openUser, blocked: attention?.waitingOn ? 1 : 0, done: doneUser, decisions: null },
    decisionIds: [], doneTodoIds: [], blockedTodoIds: [], resultIds: [],
  }), [openUser, attention, doneUser]);
  const delta = marker.previous ? compareSeenMarker(marker.previous, input) : null;
  useEffect(() => {
    // The marker advances when the line has been shown and the owner leaves the page.
    return () => { writeSeenMarker(marker.storage, marker.key, buildSeenMarker(input)); };
  }, [marker, input]);

  return <div className="goal-overview">
    <section className="goal-vitals" aria-label={t('loopx.vitals')}>
      <div className="goal-vital"><span className="goal-vital-value">{openUser}</span><span className="goal-vital-label">{t('loopx.vitalUserTodos')}</span></div>
      <div className="goal-vital"><span className="goal-vital-value">{openAgent}</span><span className="goal-vital-label">{t('loopx.vitalAgentTodos')}</span></div>
      <div className="goal-vital"><span className="goal-vital-value">{doneUser ?? '?'}</span><span className="goal-vital-label">{t('loopx.vitalDone')}</span></div>
      <div className="goal-vital"><span className="goal-vital-value">{attention?.waitingOn ?? '?'}</span><span className="goal-vital-label">{t('loopx.vitalWaitingOn')}</span></div>
    </section>
    {delta?.material ? <p className="goal-since" role="status">{t('loopx.sinceLine', delta.newlyDone, delta.newDecisions, delta.newlyBlocked)}</p> : null}
    <section className="goal-needs" aria-label={t('loopx.needsYou')}>
      <h2>{t('loopx.needsYou')}</h2>
      {openUser > 0
        ? <p className="goal-need">{t('loopx.needsYouCount', openUser)}</p>
        : <p className="goal-empty">{attention ? t('loopx.needsYouEmpty') : t('loopx.attentionUnknown')}</p>}
      {attention?.recommendedAction ? <p className="goal-next">{attention.recommendedAction}</p> : null}
    </section>
    {goal.objective ? <section className="goal-objective" aria-label={t('loopx.objective')}>
      <h2>{t('loopx.objective')}</h2>
      <p>{goal.objective}</p>
    </section> : null}
  </div>;
}

/** The page shell: masthead with the goal's identity, then the overview. Tabs arrive with later batches. */
export function GoalWorkPage({ goalId, goals, attention, failure, onRetry }: {
  goalId: string;
  goals: LoopxGoal[];
  attention: LoopxAttention[];
  failure: LoopxStatusFailure | null;
  onRetry?: () => void;
}) {
  const goal = goals.find(g => g.goalId === goalId) ?? null;
  if (!goal && !failure) return <div className="goal-page"><div className="loading-note">{t('common.loading')}</div></div>;
  if (!goal) return <div className="goal-page">
    <header className="goal-masthead"><h1>{goalId}</h1></header>
    <p role="alert" className="goal-failure">{failureLabel(failure)}</p>
    {onRetry ? <button type="button" onClick={onRetry}>{t('common.retry')}</button> : null}
  </div>;
  return <div className="goal-page">
    <header className="goal-masthead">
      <span className={`sidebar-goal-dot is-${goal.activationState}`} aria-hidden="true" />
      <h1>{goal.title}</h1>
      <span className="goal-masthead-status">{goal.status}</span>
    </header>
    <GoalOverview goal={goal} attention={attention.find(item => item.goalId === goal.goalId) ?? null} />
  </div>;
}

export function failureLabel(failure: LoopxStatusFailure | null): string {
  if (!failure) return '';
  if (failure.kind === 'not-connected') return t('loopx.notConnected');
  if (failure.kind === 'bad-data') return t('loopx.badData', failure.detail);
  return t('loopx.unavailable', failure.detail);
}
