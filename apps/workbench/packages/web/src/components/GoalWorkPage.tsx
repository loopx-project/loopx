import { useEffect, useMemo, useState } from 'react';
import { t } from '../i18n';
import type { Thread } from '../api';
import { GoalLifecycle } from './GoalLifecycle';
import { GoalAcceptance } from './GoalAcceptance';
import { GoalFiles } from './GoalFiles';
import { GoalTasks } from './GoalTasks';
import { buildSeenMarker, compareSeenMarker, readSeenMarker, seenMarkerStorage, seenMarkerStorageKey, writeSeenMarker, type SeenMarkerInput } from '../loopx/since-you-looked';
import { fetchGoalDetail, type LoopxGoalDetail } from '../loopx/goal-detail';
import { goalOf, statusWord } from '../loopx/launch';
import { joinGoalConversation } from '../loopx/join';
import type { LoopxAttention, LoopxGoal, LoopxStatusFailure } from '../loopx/status';

/** The scoped status read this page owns: the todo rows themselves, refreshed when a lifecycle change lands. */
function useGoalDetail(token: string | undefined, goalId: string, refreshKey: number) {
  const [detail, setDetail] = useState<LoopxGoalDetail | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const controller = new AbortController();
    setDetail(null);
    setFailure(null);
    fetchGoalDetail(token, goalId, { signal: controller.signal })
      .then(value => { if (alive) setDetail(value); })
      .catch(cause => {
        if (!alive || controller.signal.aborted) return;
        setFailure(cause instanceof Error ? cause.message : String(cause));
      });
    return () => { alive = false; controller.abort(); };
    // The rows reload when the Goal changed underneath (a lifecycle apply bumps refreshKey).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, goalId, refreshKey]);
  return { detail, failure };
}

/**
 * The Goal work page: the overview (Vitals, "since you looked", the rows that wait on the person,
 * the agent's own open rows) and the Goal's loochat conversations. Everything the core did not say
 * says unknown; the todo rows come from the scoped status read, which lists as many rows as it
 * carried and says when it carried fewer than it counted.
 */
export type GoalTab = 'overview' | 'tasks' | 'chat' | 'files';
export const GOAL_TABS: GoalTab[] = ['overview', 'tasks', 'chat', 'files'];

export function GoalWorkPage({ token, goalId, tab, onTab, goals, goalsLoaded, attention, failure, onRetry, onChanged, threads, onOpenThread, onBrowseFiles, defaultAgent }: {
  token: string | undefined;
  goalId: string;
  tab: GoalTab;
  onTab: (tab: GoalTab) => void;
  goals: LoopxGoal[];
  /** False while the status read is still on its way; a missing Goal is then "gone", not "loading". */
  goalsLoaded: boolean;
  attention: LoopxAttention[];
  failure: LoopxStatusFailure | null;
  onRetry?: () => void;
  onChanged: () => void;
  threads: Thread[];
  onOpenThread: (threadId: string) => void;
  onBrowseFiles: (threadId: string) => void;
  defaultAgent: string;
}) {
  const [refreshKey, setRefreshKey] = useState(0);
  const goal = goals.find(g => g.goalId === goalId) ?? null;
  const { detail } = useGoalDetail(token, goalId, refreshKey);
  if (!goal && goalsLoaded) return <div className="goal-page">
    <header className="goal-masthead"><h1>{goalId}</h1></header>
    <p className="goal-empty">{t('loopx.goalGone')}</p>
  </div>;
  if (!goal) return <div className="goal-page"><div className="loading-note">{t('common.loading')}</div></div>;
  if (!goal) return <div className="goal-page">
    <header className="goal-masthead"><h1>{goalId}</h1></header>
    <p role="alert" className="goal-failure">{failureLabel(failure)}</p>
    {onRetry ? <button type="button" onClick={onRetry}>{t('common.retry')}</button> : null}
  </div>;
  const changed = () => { setRefreshKey(key => key + 1); onChanged(); };
  return <div className="goal-page">
    <header className="goal-masthead">
      <span className={`sidebar-goal-dot is-${goal.activationState}`} aria-hidden="true" />
      <h1>{goal.title}</h1>
      <span className="goal-masthead-status">{statusWord(detail?.status ?? goal.status)}</span>
      <div className="goal-masthead-actions">
        <GoalLifecycle token={token} goal={goal} onChanged={changed} />
      </div>
      <nav className="goal-tabs" aria-label={t('loopx.tabs')}>
        <button type="button" className={tab === 'overview' ? 'active' : ''} aria-current={tab === 'overview' ? 'page' : undefined} onClick={() => onTab('overview')}>{t('loopx.tabOverview')}</button>
        <button type="button" className={tab === 'tasks' ? 'active' : ''} aria-current={tab === 'tasks' ? 'page' : undefined} onClick={() => onTab('tasks')}>{t('loopx.tabTasks')}</button>
        <button type="button" className={tab === 'chat' ? 'active' : ''} aria-current={tab === 'chat' ? 'page' : undefined} onClick={() => onTab('chat')}>{t('loopx.tabConversations')}</button>
        <button type="button" className={tab === 'files' ? 'active' : ''} aria-current={tab === 'files' ? 'page' : undefined} onClick={() => onTab('files')}>{t('loopx.tabFiles')}</button>
      </nav>
    </header>
    {tab === 'overview'
      ? <GoalOverview token={token} goal={goal} attention={attention.find(item => item.goalId === goal.goalId) ?? null} detail={detail} />
      : tab === 'tasks'
      ? <GoalTasks token={token} goalId={goal.goalId} refreshKey={refreshKey} />
      : tab === 'files'
      ? <GoalFiles token={token} goalId={goal.goalId} threads={threads} onBrowse={onBrowseFiles} onOpenConversations={() => onTab('chat')} />
      : <GoalConversations token={token} goalId={goal.goalId} title={goal.title} threads={threads} onOpenThread={onOpenThread} defaultAgent={defaultAgent} />}
  </div>;
}

/**
 * The overview tab: Vitals, "since you looked", and the rows that wait on the person. The marker
 * (what changed since the last visit) is local to the browser.
 */
export function GoalOverview({ token, goal, attention, detail }: { token?: string; goal: LoopxGoal; attention: LoopxAttention | null; detail: LoopxGoalDetail | null }) {
  const openUser = detail?.userTodos.open ?? attention?.userTodos.open ?? 0;
  const openAgent = detail?.agentTodos.open ?? attention?.agentTodos.open ?? 0;
  const doneUser = detail ? detail.userTodos.done : attention ? attention.userTodos.done : null;
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
      {openUser > 0 ? <TodoRows group={detail?.userTodos ?? null} emptyCount={openUser} /> : <p className="goal-empty">{attention ? t('loopx.needsYouEmpty') : t('loopx.attentionUnknown')}</p>}
      {attention?.recommendedAction ? <p className="goal-next">{attention.recommendedAction}</p> : null}
    </section>
    <section className="goal-agent-todos" aria-label={t('loopx.agentTodos')}>
      <h2>{t('loopx.agentTodos')}</h2>
      {openAgent > 0 ? <TodoRows group={detail?.agentTodos ?? null} emptyCount={openAgent} /> : <p className="goal-empty">{t('loopx.agentTodosEmpty')}</p>}
    </section>
    {goal.objective ? <section className="goal-objective" aria-label={t('loopx.objective')}>
      <h2>{t('loopx.objective')}</h2>
      <p>{goal.objective}</p>
    </section> : null}
    <GoalAcceptance token={token} goal={goal} />
  </div>;
}

/** The todo rows the scoped status listed; when it listed none of several, the count still says so. */
function TodoRows({ group, emptyCount }: { group: { items: { id: string; text: string; status: string }[]; total: number; complete: boolean } | null; emptyCount: number }) {
  if (!group || group.items.length === 0) return <p className="goal-need">{t('loopx.needsYouCount', emptyCount)}</p>;
  return <>
    <ul className="goal-todos">
      {group.items.map(item => <li key={item.id} className={item.status === 'open' ? 'open' : 'done'}>
        <span className="goal-todo-state" aria-hidden="true" />
        <span className="goal-todo-text">{item.text}</span>
      </li>)}
    </ul>
    {!group.complete ? <p className="goal-todos-more">{t('loopx.todosTruncated', Math.max(group.total - group.items.length, 0))}</p> : null}
  </>;
}

/** The Goal's loochat conversations: the threads bound to it, and a way to start another. */
function GoalConversations({ token, goalId, title, threads, onOpenThread, defaultAgent }: {
  token: string | undefined;
  goalId: string;
  title: string;
  threads: Thread[];
  onOpenThread: (threadId: string) => void;
  defaultAgent: string;
}) {
  const bound = threads.filter(thread => goalOf(thread)?.goalId === goalId);
  const [joining, setJoining] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A new conversation joins the Goal's own line: the agent its latest conversation used, else the default.
  const agent = bound[0]?.agent ?? defaultAgent;
  const join = () => {
    if (joining) return;
    setJoining(true);
    setError(null);
    joinGoalConversation(token, { goalId, agent })
      .then(result => onOpenThread(result.threadId))
      .catch(cause => setError(cause instanceof Error ? cause.message : String(cause)))
      .finally(() => setJoining(false));
  };
  return <section className="goal-threads" aria-label={t('loopx.tabConversations')}>
    <button type="button" className="goal-thread-new" disabled={joining || !agent} onClick={join}>{t('loopx.newThread')}</button>
    {error ? <p role="alert" className="goal-failure">{error}</p> : null}
    {bound.length ? bound.map(thread => (
      <button key={thread.id} type="button" className={`goal-thread is-${thread.status}`} onClick={() => onOpenThread(thread.id)}>
        <span className="goal-thread-title">{thread.title === thread.id ? title : thread.title}</span>
        <span className="goal-thread-status">{thread.status === 'running' ? t('loopx.running') : thread.preview ?? ''}</span>
      </button>
    )) : <p className="goal-empty">{t('loopx.noThreads')}</p>}
  </section>;
}

export function failureLabel(failure: LoopxStatusFailure | null): string {
  if (!failure) return '';
  if (failure.kind === 'not-connected') return t('loopx.notConnected');
  if (failure.kind === 'bad-data') return t('loopx.badData', failure.detail);
  if (failure.detail === 'loopx_core_unavailable') return t('loopx.coreDown');
  return t('loopx.unavailable', failure.detail);
}
