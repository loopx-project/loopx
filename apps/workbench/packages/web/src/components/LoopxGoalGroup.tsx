import { useEffect, useRef, useState } from 'react';
import '../loopx/loopx.css';
import { fetchGoalBar, goalOf, statusWord, type GoalBarRead } from '../loopx/launch';
import { wakeOwner } from '../loopx/session';
import type { Thread } from '../api';
import { t } from '../i18n';
import { IconCrab } from './icons';

const WAKE_WORDS = { app: 'loopx.panel.wake.app', cli: 'loopx.panel.wake.cli', ide: 'loopx.panel.wake.ide', workbench: 'loopx.panel.wake.workbench', unknown: 'loopx.panel.wake.unknown' } as const;

/**
 * The Goal a conversation is on, first in the session panel: its name and where it stands, what
 * is being done now, who has open work, and who wakes it. The conversation page itself carries no
 * Goal strip. A Goal the core no longer has is only named, struck through.
 */
export function LoopxGoalGroup({ token, thread, onOpenGoal }: { token: string | undefined; thread: Thread; onOpenGoal: (goalId: string) => void }) {
  const info = thread.loopxInfo;
  const bound = Boolean(goalOf(thread));
  const [read, setRead] = useState<GoalBarRead | null>(null);
  const alive = useRef(true);
  // The core moves on its own (its heartbeat advances the Goal with no turn here), so the panel polls while it is open.
  useEffect(() => {
    if (!info || info.missing || !bound) { setRead(null); return; }
    alive.current = true;
    const load = () => { fetchGoalBar(token, thread.id).then((next) => { if (alive.current) setRead(next); }, () => { if (alive.current) setRead(null); }); };
    load();
    const timer = setInterval(load, 20000);
    return () => { alive.current = false; clearInterval(timer); };
  }, [thread.id, token, info?.goalId, info?.missing, bound]);
  const wasRunning = useRef(thread.status === 'running');
  useEffect(() => {
    if (wasRunning.current && thread.status !== 'running' && bound && info && !info.missing) fetchGoalBar(token, thread.id, true).then((next) => { if (alive.current) setRead(next); }, () => undefined);
    wasRunning.current = thread.status === 'running';
  }, [thread.status]);
  if (!info) return null;

  const state = read?.state ?? null;
  const title = state && state.title !== info.goalId ? state.title : info.goalTitle;
  const open = (g: { open: number; total: number } | undefined) => g ? `${g.open}/${g.total}` : '—';
  return (
    <section className="overview-group open loopx-group" aria-label={t('loopx.panel.title')}>
      <div className="overview-head">
        <div className="overview-toggle loopx-group-head">
          <span className="overview-head-icon"><IconCrab size={14} /></span>
          <strong>{t('loopx.panel.title')}</strong>
          <span className="overview-summary">{info.missing ? t('sidebar.loopxDeleted') : state ? statusWord(state.status) : ''}</span>
        </div>
      </div>
      <div className="overview-body loopx-group-body">
        <div className={`loopx-group-name${info.missing ? ' gone' : ''}`} title={info.goalId}>{title}</div>
        {!info.missing && state?.summary && <div className="loopx-group-line"><span>{t('loopx.panel.current')}</span><span className="loopx-group-value" title={state.summary}>{state.summary}</span></div>}
        {!info.missing && state && <div className="loopx-group-line"><span>{t('loopx.panel.todos')}</span><span className="loopx-group-value">{t('loopx.goal.agentTodos')} {open(state.agentTodos)} · {t('loopx.goal.yourTodos')} {open(state.userTodos)}</span></div>}
        {!info.missing && <div className="loopx-group-line"><span>{t('loopx.panel.heartbeat')}</span><span className="loopx-group-value">{t('loopx.panel.wakenBy', t(WAKE_WORDS[wakeOwner(info, thread.origin)]))}</span></div>}
        {!info.missing && bound && !read && <p className="loopx-group-note" role="status">{t('loopx.goal.reading')}</p>}
        {!info.missing && read?.freshness === 'unavailable' && <p className="loopx-group-note" role="status">{t('loopx.goal.stateUnavailable')}</p>}
        {!info.missing && <button type="button" className="overview-more loopx-group-open" onClick={() => onOpenGoal(info.goalId)}>{t('loopx.panel.openGoal')}</button>}
      </div>
    </section>
  );
}
