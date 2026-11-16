import { useEffect, useRef, useState } from 'react';
import loopxIconLight from '../assets/loo-icon-paper.svg';
import loopxIconDark from '../assets/loo-icon-dark.svg';
import '../loopx/loopx.css';
import { applyGoal, defaultGoalPermission, fetchGoalBar, LoopxError, permissionCopy, previewGoal, statusWord,
  type GoalAgentCapability, type GoalBarRead, type GoalBinding, type GoalPermission, type GoalPreview } from '../loopx/launch';
import { t } from '../i18n';
import { Dialog } from './Dialog';
import { IconCheck, IconHand, IconShield } from './icons';
import { Popover } from './Popover';

/** The LoopX mark next to the voice button: turns a new conversation into a Goal conversation. */
export function LoopxToggle({ active, onToggle, disabledReason }: { active: boolean; onToggle: () => void; disabledReason?: string }) {
  const title = disabledReason ?? (active ? t('loopx.goal.cancelMode') : t('loopx.goal.handOver'));
  return (
    <button type="button" className={`tool-btn composer-loopx${active ? ' active' : ''}`} disabled={Boolean(disabledReason)} onClick={onToggle} title={title} aria-label={title} aria-pressed={active}>
      {/* The mark follows the theme's own ink, like every other icon; active wears it in white on black. */}
      <img className="brand-icon loo-light" src={loopxIconLight} alt="" width={18} height={18} />
      <img className="brand-icon loo-dark" src={loopxIconDark} alt="" width={18} height={18} />
      {active && <span className="composer-loopx-label">LoopX</span>}
    </button>
  );
}

/** The two levels of a Goal conversation, in the place the ordinary conversation has its three. */
export function GoalPermissionPicker({ capability, value, onChange }: { capability: GoalAgentCapability | undefined; value: GoalPermission; onChange: (p: GoalPermission) => void }) {
  const Icon = value === 'workspace_auto' ? IconShield : IconHand;
  return (
    <Popover label={t('loopx.goal.permission')} triggerClassName="tool-btn icon-only composer-permission composer-goal-permission" triggerLabel={`${t('loopx.goal.permission')}：${permissionCopy(value).label}`} triggerTitle={permissionCopy(value).hint}
      trigger={<><Icon size={16} /><span className="composer-goal-permission-name">{permissionCopy(value).label}</span></>}>
      {(close) => <>
        <div className="pop-title">{t('loopx.goal.permissionTitle')}</div>
        {(capability?.permissions ?? []).map((p) => {
          const ItemIcon = p.id === 'workspace_auto' ? IconShield : IconHand;
          return (
            <button key={p.id} type="button" role="menuitemradio" aria-checked={p.id === value} aria-disabled={!p.available} disabled={!p.available}
              className="pop-item" title={p.reason} onClick={() => { onChange(p.id); close(); }}>
              <span className="pop-item-icon"><ItemIcon size={18} /></span>
              <span className="pop-item-text">
                <span className="pop-item-label">{permissionCopy(p.id).label}</span>
                <span className="pop-item-hint">{p.available ? permissionCopy(p.id).hint : p.reason}</span>
              </span>
              {p.id === value && <span className="pop-item-check"><IconCheck size={16} /></span>}
            </button>
          );
        })}
      </>}
    </Popover>
  );
}

/** What a Goal conversation shows above its input: the Goal, where it stands, and under what permission. */
export function GoalBar({ token, threadId, binding, running, title }: { token: string | undefined; threadId: string; binding: GoalBinding; running: boolean; title?: string }) {
  const [read, setRead] = useState<GoalBarRead | null>(null);
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  const load = async (refresh: boolean) => {
    setBusy(true);
    try { const next = await fetchGoalBar(token, threadId, refresh); if (alive.current) setRead(next); }
    catch (error) { if (alive.current) setRead((prev) => ({ binding, state: prev?.state ?? null, freshness: 'unavailable', observedAt: new Date().toISOString(), error: error instanceof Error ? error.message : String(error) })); }
    finally { if (alive.current) setBusy(false); }
  };
  // The core moves on its own (its heartbeat advances the Goal with no turn here), so the bar polls; a finished turn reads at once.
  useEffect(() => { alive.current = true; void load(false); const timer = setInterval(() => void load(false), 20000); return () => { alive.current = false; clearInterval(timer); }; }, [threadId, token]);
  const wasRunning = useRef(running);
  useEffect(() => { if (wasRunning.current && !running) void load(true); wasRunning.current = running; }, [running]);

  const state = read?.state ?? null;
  const open = (g: { open: number; total: number } | undefined) => g ? `${g.open}/${g.total}` : '—';
  return (
    <div className={`goal-bar${read?.freshness === 'unavailable' ? ' stale' : ''}`} role="status" data-testid="goal-bar">
      <img className="brand-icon loo-light" src={loopxIconLight} alt="" width={16} height={16} />
      <img className="brand-icon loo-dark" src={loopxIconDark} alt="" width={16} height={16} />
      <span className="goal-bar-title" title={binding.goalId}>{state && state.title !== binding.goalId ? state.title : title || binding.goalId}</span>
      {state ? <span className="goal-bar-status">{statusWord(state.status)}</span>
        : <span className="goal-bar-status warn">{read ? t('loopx.goal.stateUnavailable') : t('loopx.goal.reading')}</span>}
      {state && <span className="goal-bar-todos" title={t('loopx.goal.openTotal')}>{t('loopx.goal.agentTodos')} {open(state.agentTodos)} · {t('loopx.goal.yourTodos')} {open(state.userTodos)}</span>}
      <span className="goal-bar-permission" title={permissionCopy(binding.permission).hint}>{permissionCopy(binding.permission).label}</span>
      <button type="button" className="goal-bar-refresh" disabled={busy} onClick={() => void load(true)} title={read?.error ?? t('loopx.goal.refresh')} aria-label={t('loopx.goal.refreshState')}>{busy ? '…' : '↻'}</button>
    </div>
  );
}

interface ConfirmProps {
  token: string | undefined;
  agent: string;
  agentLabel: string;
  cwd: string;
  permission: GoalPermission;
  text: string;
  model: string | null;
  effort: string | null;
  onCreated: (threadId: string) => void;
  onCancel: () => void;
}

/**
 * The review a Goal needs before it exists: the core checks the request (workspace, agent, boundary)
 * and shows what it will create; nothing is made until the person confirms. Editing the completion
 * or boundary asks the core again.
 */
export function GoalConfirm({ token, agent, agentLabel, cwd, permission, text, model, effort, onCreated, onCancel }: ConfirmProps) {
  const [completion, setCompletion] = useState('');
  const [boundary, setBoundary] = useState('');
  const [review, setReview] = useState<{ status: 'loading' } | { status: 'ready'; preview: GoalPreview } | { status: 'error'; error: LoopxError | Error }>({ status: 'loading' });
  const [creating, setCreating] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setReview({ status: 'loading' });
    const timer = setTimeout(() => {
      previewGoal(token, { agent, cwd, permission, objective: text, completion, boundary }, controller.signal)
        .then((preview) => setReview({ status: 'ready', preview }), (error) => { if (!controller.signal.aborted) setReview({ status: 'error', error }); });
    }, 500);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [token, agent, cwd, permission, text, completion, boundary]);

  const create = async () => {
    if (review.status !== 'ready') return;
    setCreating(true); setFailure(null);
    try { const done = await applyGoal(token, { proposalId: review.preview.proposalId, agent, permission, model, effort }); onCreated(done.threadId); }
    catch (error) { setFailure(error instanceof Error ? error.message : String(error)); setCreating(false); }
  };

  const error = review.status === 'error' ? review.error : null;
  return (
    <Dialog title={t('loopx.goal.createTitle')} confirmLabel={creating ? t('loopx.goal.creating') : t('loopx.goal.createGo')}
      confirmDisabled={review.status !== 'ready' || creating} className="goal-confirm" onConfirm={() => void create()} onCancel={onCancel}>
      <div className="goal-confirm-body">
        <label className="goal-confirm-field"><span>{t('loopx.goal.goal')}</span><div className="goal-confirm-objective">{text}</div></label>
        <label className="goal-confirm-field"><span>{t('loopx.goal.completion')}</span>
          <textarea rows={2} value={completion} onChange={(e) => setCompletion(e.target.value)} placeholder={t('loopx.goal.completionHint')} /></label>
        <label className="goal-confirm-field"><span>{t('loopx.goal.boundary')}</span>
          <textarea rows={2} value={boundary} onChange={(e) => setBoundary(e.target.value)} placeholder={t('loopx.goal.boundaryHint')} /></label>
        <dl className="goal-confirm-facts">
          <dt>{t('loopx.goal.agent')}</dt><dd>{agentLabel}</dd>
          <dt>{t('loopx.goal.permission')}</dt><dd>{permissionCopy(permission).label} · {permissionCopy(permission).hint}</dd>
          <dt>{t('loopx.goal.workspace')}</dt><dd><code>{review.status === 'ready' ? review.preview.workspace : cwd}</code></dd>
        </dl>
        {review.status === 'loading' && <p className="goal-confirm-note" role="status">{t('loopx.goal.reviewing')}</p>}
        {review.status === 'ready' && <p className="goal-confirm-note ok" role="status">{t('loopx.goal.reviewed')}{review.preview.title}</p>}
        {error && <div className="goal-confirm-error" role="alert">
          <p>{error.message}</p>
          {error instanceof LoopxError && error.code === 'workspace_not_granted' && error.candidates?.length ? <>
            <p>{t('loopx.goal.notGranted')}</p>
            <ul>{error.candidates.map((c) => <li key={c.title}><code>{c.title}</code></li>)}</ul></> : null}
          {error instanceof LoopxError && error.code === 'loopx_core_unavailable' && <p>{t('loopx.goal.coreDown')}</p>}
        </div>}
        {failure && <div className="goal-confirm-error" role="alert"><p>{failure}</p></div>}
      </div>
    </Dialog>
  );
}
