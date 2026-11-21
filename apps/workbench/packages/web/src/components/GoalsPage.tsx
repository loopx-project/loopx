import { useMemo, useState } from 'react';
import { t } from '../i18n';
import { IconPin, IconSearch } from './icons';
import { GoalLifecycle } from './GoalLifecycle';
import { useGoalPins } from '../loopx/goal-picks';
import { needsYouGoalIds, sortGoalTree, type LoopxAttention, type LoopxGoal } from '../loopx/status';

export type GoalsFilter = 'all' | 'needs-you' | 'active' | 'stopped';
type Filter = GoalsFilter;
const FILTERS: Filter[] = ['all', 'needs-you', 'active', 'stopped'];

const shortDate = (iso: string | null): string => {
  if (!iso) return '';
  const at = Date.parse(iso);
  if (!Number.isFinite(at)) return iso;
  const date = new Date(at);
  const today = new Date();
  return date.toDateString() === today.toDateString()
    ? date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : date.toLocaleDateString();
};

/** The whole Goal list in one page: searched, filtered, grouped by where each Goal stands — the
 * sidebar's all-goals entry behind its five rows. Each row is what the shared status read knows:
 * the name, the todo count, the agents the core named, its most important open todo, the last
 * touch; anything the read did not carry stays empty rather than invented. Stopping, resuming and
 * deleting go through the same typed-action confirmation the work page uses. */
export function GoalsPage({ token, goals, attention, activeGoalId, initialFilter = 'all', onOpenGoal, onChanged }: {
  token: string | undefined;
  goals: LoopxGoal[];
  attention: LoopxAttention[];
  activeGoalId: string | null;
  /** The filter it opens on, when another page sent the person to one group. */
  initialFilter?: GoalsFilter;
  onOpenGoal: (goalId: string) => void;
  onChanged: () => void;
}) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>(initialFilter);
  const [pins, setPin] = useGoalPins();
  const needsYou = useMemo(() => needsYouGoalIds(attention), [attention]);
  const byId = useMemo(() => new Map(goals.map(goal => [goal.goalId, goal])), [goals]);
  const attentionOf = (goalId: string) => attention.find(item => item.goalId === goalId) ?? null;

  const searched = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return goals;
    return goals.filter(goal => {
      const item = attentionOf(goal.goalId);
      return [goal.title, goal.objective ?? '', item?.topTodo?.text ?? '', item?.recommendedAction ?? '']
        .some(text => text.toLowerCase().includes(q));
    });
    // attentionOf is a lookup over the memoized attention; goals decides the rows.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [goals, attention, query]);

  const groupOf = (goal: LoopxGoal): Filter => {
    if (goal.activationState !== 'active') return 'stopped';
    return needsYou.has(goal.goalId) ? 'needs-you' : 'active';
  };

  const tree = useMemo(() => sortGoalTree(searched), [searched]);
  const groups = useMemo(() => {
    const buckets: Record<Filter, LoopxGoal[]> = { all: [], 'needs-you': [], active: [], stopped: [] };
    for (const goal of tree) buckets[groupOf(goal)].push(goal);
    return buckets;
  }, [tree, needsYou]);

  const row = (goal: LoopxGoal, depth: number) => {
    const item = attentionOf(goal.goalId);
    const openTodos = (item?.userTodos.open ?? 0) + (item?.agentTodos.open ?? 0);
    const totalTodos = (item?.userTodos.total ?? 0) + (item?.agentTodos.total ?? 0);
    const task = item?.topTodo?.text ?? item?.recommendedAction ?? null;
    const recent = item?.lastActivityAt ?? goal.updatedAt ?? null;
    const pinned = pins.includes(goal.goalId);
    const top = depth === 0;
    const dot = goal.activationState !== 'active' ? 'is-stopped' : needsYou.has(goal.goalId) ? 'is-needs-you' : 'is-active';
    return <div key={goal.goalId} className={`goals-row${top ? '' : ' is-child'}`} style={{ ['--depth' as string]: String(depth) }}>
      <button type="button" className="goals-row-main" onClick={() => onOpenGoal(goal.goalId)}>
        <span className={`goals-dot ${dot}`} aria-hidden="true" />
        <span className="goals-row-title">
          {goal.title}
          {needsYou.has(goal.goalId) && (item?.userTodos.open ?? 0) > 0
            ? <span className="goals-row-badge">{item!.userTodos.open}</span> : null}
        </span>
        <span className="goals-row-sub">
          {goal.agents.length ? <span className="goals-row-agents">{goal.agents.join(', ')}</span> : null}
          {task ? <span className="goals-row-task">{task}</span> : null}
        </span>
        <span className="goals-row-count">{totalTodos > 0 ? t('goals.taskCount', totalTodos) : ''}</span>
        <span className="goals-row-recent" title={recent ?? ''}>{recent ? shortDate(recent) : ''}</span>
      </button>
      {top ? <button type="button" className={`icon-btn goals-pin${pinned ? ' on' : ''}`}
        aria-pressed={pinned} aria-label={pinned ? t('goals.unpin') : t('goals.pin')}
        title={pinned ? t('goals.unpin') : t('goals.pin')}
        onClick={() => setPin(goal.goalId, !pinned)}>
        <IconPin size={15} />
      </button> : null}
      {top ? <GoalLifecycle token={token} goal={goal} onChanged={onChanged} /> : null}
    </div>;
  };

  // Children sit indented under their parent wherever the family landed in this group's walk.
  const rowsOf = (list: LoopxGoal[]) => {
    const visible = new Set(list.map(goal => goal.goalId));
    const depthOf = (goal: LoopxGoal): number => {
      let depth = 0;
      let current = goal;
      while (current.parentId) {
        const parent = byId.get(current.parentId);
        if (!parent || !visible.has(parent.goalId)) break;
        current = parent;
        depth += 1;
      }
      return depth;
    };
    return list.map(goal => row(goal, depthOf(goal)));
  };

  const section = (key: Exclude<Filter, 'all'>) => groups[key].length
    ? <section className="goals-group" aria-label={t(`goals.group.${key}`)}>
      <h2>{t(`goals.group.${key}`)}<span>{groups[key].length}</span></h2>
      {rowsOf(groups[key])}
    </section> : null;

  return <div className="goals-page">
    <header className="goals-head">
      <h1>{t('goals.title')}</h1>
      <label className="goals-search">
        <IconSearch size={15} />
        <input type="search" maxLength={120} value={query} placeholder={t('goals.search')}
          aria-label={t('goals.search')} onChange={e => setQuery(e.target.value)} />
      </label>
    </header>
    <div className="goals-filters" role="group" aria-label={t('goals.filters')}>
      {FILTERS.map(key => <button key={key} type="button" aria-pressed={filter === key} onClick={() => setFilter(key)}>
        {t(`goals.filter.${key}`)}<span className="goals-filter-count">{key === 'all' ? goals.length : groups[key].length}</span>
      </button>)}
    </div>
    {filter === 'all' || filter === 'needs-you' ? section('needs-you') : null}
    {filter === 'all' || filter === 'active' ? section('active') : null}
    {filter === 'all' || filter === 'stopped' ? section('stopped') : null}
    {!tree.length ? <p className="goals-empty">{query ? t('goals.noMatch') : t('goals.none')}</p> : null}
  </div>;
}
