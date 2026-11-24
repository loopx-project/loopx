import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { t } from '../i18n';
import { fetchGoalTasks, laneTasks, owners, TASK_FILTERS, DEFAULT_BROWSE, type Browse, type GoalTasks as GoalTasksData, type Task, type TaskFilter, type TaskGroup } from '../loopx/tasks';
import { failureLabel, loopxFailureOf, type LoopxStatusFailure } from '../loopx/status';
import { Popover } from './Popover';
import { IconCheck, IconChevron } from './icons';

const REFRESH_MS = 15000;

/** The Goal's task rows, read on their own so a write elsewhere (`refreshKey`) can reload just this. */
function useGoalTasks(token: string | undefined, goalId: string, refreshKey: number) {
  const [state, setState] = useState<{ data: GoalTasksData | null; failure: LoopxStatusFailure | null; loading: boolean }>({ data: null, failure: null, loading: true });
  useEffect(() => {
    let alive = true, timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const once = async () => {
      try {
        const data = await fetchGoalTasks(token, goalId, controller.signal);
        if (alive) setState({ data, failure: null, loading: false });
      } catch (cause) {
        if (!alive || controller.signal.aborted) return;
        // A failed refresh keeps the rows already on screen; the failure is shown beside them.
        setState(prev => ({ data: prev.data, failure: loopxFailureOf(cause), loading: false }));
      } finally {
        if (alive) timer = setTimeout(() => void once(), REFRESH_MS);
      }
    };
    setState({ data: null, failure: null, loading: true });
    void once();
    return () => { alive = false; clearTimeout(timer); controller.abort(); };
  }, [token, goalId, refreshKey]);
  return state;
}

const filterLabel = (filter: TaskFilter) => t(`loopx.tasks.filter.${filter}`);

function TaskCard({ task, expanded, onToggle, renderActions }: { task: Task; expanded: boolean; onToggle: () => void; renderActions?: (task: Task) => ReactNode }) {
  const reason = task.state === 'blocked' ? task.blockedReason : task.state === 'deferred' ? task.resumeWhen : null;
  return <div className={`goal-task is-${task.state}${expanded ? ' is-expanded' : ''}`} data-task-id={task.id} data-role={task.role}>
    <button type="button" className="goal-task-main" aria-expanded={expanded} onClick={onToggle}>
      <strong>{task.title}</strong>
      <small>
        {task.priority ? <span className={`goal-task-priority is-${task.priority.toLowerCase()}`}>{task.priority}</span> : null}
        <span className={`goal-task-state is-${task.state}`}>{task.role === 'user' && task.state === 'open' ? t('loopx.tasks.needsYou') : t(`loopx.tasks.state.${task.state}`)}</span>
        <span>{task.role === 'user' ? t('loopx.tasks.you') : task.owner ?? t('loopx.tasks.unassigned')}</span>
      </small>
      {reason ? <span className="goal-task-reason">{reason}</span> : null}
    </button>
    {renderActions ? <div className="goal-task-actions" data-slot="task-actions">{renderActions(task)}</div> : null}
    {expanded ? <dl className="goal-task-details">
      <div><dt>{t('loopx.tasks.id')}</dt><dd><code>{task.id}</code></dd></div>
      {task.taskClass ? <div><dt>{t('loopx.tasks.class')}</dt><dd>{task.taskClass}</dd></div> : null}
      {task.updatedAt && Number.isFinite(Date.parse(task.updatedAt)) ? <div><dt>{t('loopx.tasks.updated')}</dt><dd>{new Date(task.updatedAt).toLocaleString()}</dd></div> : null}
    </dl> : null}
  </div>;
}

function Lane({ label, tone, tasks, hidden, expanded, onToggle, renderActions, empty }: {
  label: string; tone: 'attention' | 'progress' | 'done'; tasks: Task[]; hidden: number; expanded: string | null;
  onToggle: (id: string) => void; renderActions?: (task: Task) => ReactNode; empty: string;
}) {
  const body = <>
    {tasks.map(task => <TaskCard key={`${task.role}:${task.id}`} task={task} expanded={expanded === task.id} onToggle={() => onToggle(task.id)} renderActions={renderActions} />)}
    {!tasks.length ? <p className="goal-task-empty">{empty}</p> : null}
    {hidden > 0 ? <p className="goal-task-more" role="status">{t('loopx.tasks.more', hidden)}</p> : null}
  </>;
  return <details className={`goal-task-group tone-${tone}`} open><summary><strong>{label}</strong><span>{tasks.length}</span></summary><div className="goal-task-rows">{body}</div></details>;
}

const sumHidden = (g: TaskGroup, kind: 'open' | 'done') => (kind === 'open' ? g.hiddenOpen : g.hiddenDone);

/**
 * The Goal's Tasks tab: the person's own open tasks, the agents' work, and what is done, as a grouped list, with search and filters by state and owner. Reading only; `renderActions` is where the task
 * writes (complete, claim, archive) put their buttons on each card.
 */
export function GoalTasks({ token, goalId, refreshKey = 0, renderActions }: {
  token: string | undefined;
  goalId: string;
  /** Bump to reload the rows after a write. */
  refreshKey?: number;
  /** Per-card action buttons. Nothing is drawn when omitted. */
  renderActions?: (task: Task) => ReactNode;
}) {
  const { data, failure, loading } = useGoalTasks(token, goalId, refreshKey);
  const [browse, setBrowse] = useState<Browse>(() => ({ ...DEFAULT_BROWSE, view: 'list' }));
  const [expanded, setExpanded] = useState<string | null>(null);
  useEffect(() => { setBrowse(b => ({ ...b, query: '', filter: 'all', owner: 'all' })); setExpanded(null); }, [goalId]);
  const change = (patch: Partial<Browse>) => {
    setBrowse(b => ({ ...b, ...patch }));
  };
  const lanes = useMemo(() => data ? laneTasks(data, browse) : null, [data, browse]);
  const who = useMemo(() => data ? owners(data) : { ids: [], unassigned: false }, [data]);
  const filtered = browse.query.trim() !== '' || browse.filter !== 'all' || browse.owner !== 'all';
  const toggle = (id: string) => setExpanded(current => current === id ? null : id);
  // "N more" only when nothing narrows the view: a filter hides rows on purpose, and the note would blame the core.
  const more = (g: TaskGroup, kind: 'open' | 'done') => filtered ? 0 : sumHidden(g, kind);

  if (loading && !data) return <div className="loading-note">{t('common.loading')}</div>;
  if (!data || !lanes) return <p role="alert" className="goal-failure">{failureLabel(failure)}</p>;
  const showUser = browse.filter === 'all' || browse.filter === 'needsYou';
  const showTodo = browse.filter !== 'needsYou' && browse.filter !== 'done';
  const showDone = browse.filter === 'all' || browse.filter === 'done';
  const total = lanes.needsYou.length + lanes.todo.length + lanes.done.length;
  const ownerOptions = [
    { id: 'all', label: t('loopx.tasks.allOwners', who.ids.length) },
    ...(who.unassigned ? [{ id: '', label: t('loopx.tasks.unassigned') }] : []),
    ...who.ids.map(id => ({ id, label: id })),
  ];
  return <section className="goal-tasks is-list" aria-label={t('loopx.tasks.title')}>
    {failure ? <p role="alert" className="goal-failure">{failureLabel(failure)}</p> : null}
    <div className="goal-tasks-controls">
      <input type="search" maxLength={256} value={browse.query} aria-label={t('loopx.tasks.search')} placeholder={t('loopx.tasks.search')} onChange={e => change({ query: e.target.value })} />
      <div className="goal-tasks-filters" role="group" aria-label={t('loopx.tasks.filterLabel')}>
        {TASK_FILTERS.map(filter => <button key={filter} type="button" aria-pressed={browse.filter === filter} onClick={() => change({ filter })}>{filterLabel(filter)}</button>)}
        {filtered ? <button type="button" onClick={() => change({ query: '', filter: 'all', owner: 'all' })}>{t('loopx.tasks.clear')}</button> : null}
      </div>
      {who.ids.length + (who.unassigned ? 1 : 0) > 1 || browse.owner !== 'all' ? <Popover label={t('loopx.tasks.owner')} triggerClassName="goal-tasks-owner"
        trigger={<><span>{ownerOptions.find(option => option.id === browse.owner)?.label ?? browse.owner}</span><IconChevron size={12} open={false} /></>}>
        {close => ownerOptions.map(option => <button key={option.id} type="button" role="menuitemradio" className="pop-item" aria-checked={browse.owner === option.id}
          onClick={() => { change({ owner: option.id }); close(); }}><span className="pop-item-label">{option.label}</span>{browse.owner === option.id && <IconCheck size={14} />}</button>)}
      </Popover> : null}
    </div>
    <div className="goal-tasks-grouped">
      {showUser ? <Lane label={t('loopx.tasks.lane.needsYou')} tone="attention" tasks={lanes.needsYou} hidden={more(data.user, 'open')} expanded={expanded} onToggle={toggle} renderActions={renderActions} empty={t('loopx.tasks.empty.needsYou')} /> : null}
      {showTodo ? <Lane label={t('loopx.tasks.lane.todo')} tone="progress" tasks={lanes.todo} hidden={more(data.agent, 'open')} expanded={expanded} onToggle={toggle} renderActions={renderActions} empty={t('loopx.tasks.empty.todo')} /> : null}
      {showDone ? <Lane label={t('loopx.tasks.lane.done')} tone="done" tasks={lanes.done} hidden={more(data.agent, 'done') + more(data.user, 'done')} expanded={expanded} onToggle={toggle} renderActions={renderActions} empty={t('loopx.tasks.empty.done')} /> : null}
    </div>
    {total === 0 && filtered ? <p className="goal-task-empty" role="status">{t('loopx.tasks.noMatch')}</p> : null}
  </section>;
}
