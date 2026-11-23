import { useEffect, useRef, useState } from 'react';
import '../loopx/loopx.css';
import { applyGoal, defaultGoalPermission, fetchGoalBar, fetchGoalCapabilities, fetchGoalWorkspaces, LoopxError, permissionCopy, previewGoal, statusWord,
  type GoalAgentCapability, type GoalBarRead, type GoalBinding, type GoalPermission, type GoalPreview, type GoalWorkspace } from '../loopx/launch';
import { t } from '../i18n';
import { Dialog } from './Dialog';
import { IconCheck, IconHand, IconShield, LooMark } from './icons';
import { Popover } from './Popover';

/** The LoopX mark next to the voice button: turns a new conversation into a Goal conversation. */
export function LoopxToggle({ active, onToggle, disabledReason }: { active: boolean; onToggle: () => void; disabledReason?: string }) {
  const title = disabledReason ?? (active ? t('loopx.goal.cancelMode') : t('loopx.goal.handOver'));
  return (
    <button type="button" className={`tool-btn composer-loopx${active ? ' active' : ''}`} disabled={Boolean(disabledReason)} onClick={onToggle} title={title} aria-label={title} aria-pressed={active}>
      {/* One mark, drawn in currentColor: the theme's ink at rest, white on the black active pill. */}
      <LooMark size={18} />
      {active && <span className="composer-loopx-label">LoopX</span>}
    </button>
  );
}

/** The two levels of a Goal conversation, in the place the ordinary conversation has its three. */
export function GoalPermissionPicker({ capability, value, onChange }: { capability: GoalAgentCapability | undefined; value: GoalPermission; onChange: (p: GoalPermission) => void }) {
  const Icon = value === 'workspace_auto' ? IconShield : IconHand;
  return (
    <Popover label={t('loopx.goal.permission')} triggerClassName="tool-btn icon-only composer-permission composer-goal-permission" triggerLabel={`${t('loopx.goal.permission')}：${permissionCopy(value, capability).label}`} triggerTitle={permissionCopy(value, capability).hint}
      trigger={<><Icon size={16} /><span className="composer-goal-permission-name">{permissionCopy(value, capability).label}</span></>}>
      {(close) => <>
        <div className="pop-title">{t('loopx.goal.permissionTitle')}</div>
        {(capability?.permissions ?? []).map((p) => {
          const ItemIcon = p.id === 'workspace_auto' ? IconShield : IconHand;
          return (
            <button key={p.id} type="button" role="menuitemradio" aria-checked={p.id === value} aria-disabled={!p.available} disabled={!p.available}
              className="pop-item" title={p.reason} onClick={() => { onChange(p.id); close(); }}>
              <span className="pop-item-icon"><ItemIcon size={18} /></span>
              <span className="pop-item-text">
                <span className="pop-item-label">{permissionCopy(p.id, capability).label}</span>
                <span className="pop-item-hint">{p.available ? permissionCopy(p.id, capability).hint : p.reason}</span>
              </span>
              {p.id === value && <span className="pop-item-check"><IconCheck size={16} /></span>}
            </button>
          );
        })}
      </>}
    </Popover>
  );
}

interface ConfirmProps {
  token: string | undefined;
  agent: string;
  agentLabel: (agent: string) => string;
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
export function GoalConfirm({ token, agent: draftAgent, agentLabel, cwd: draftCwd, permission: draftPermission, text, model, effort, onCreated, onCancel }: ConfirmProps) {
  // The composer's choices are where the review starts; agent, permission and directory can still be changed here.
  const [agent, setAgent] = useState(draftAgent);
  const [permission, setPermission] = useState(draftPermission);
  const [cwd, setCwd] = useState(draftCwd);
  const [agents, setAgents] = useState<GoalAgentCapability[]>([]);
  const [workspaces, setWorkspaces] = useState<GoalWorkspace[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    fetchGoalCapabilities(token, controller.signal).then(setAgents, () => undefined);
    fetchGoalWorkspaces(token, controller.signal).then((list) => {
      setWorkspaces(list);
      // A directory the core has not granted cannot hold a Goal: start from the first one it has.
      const here = list.filter((w) => w.path);
      if (here.length && !here.some((w) => w.path === draftCwd)) setCwd(here[0].path!);
    }, () => undefined);
    return () => controller.abort();
  }, [token, draftCwd]);
  const capability = agents.find((a) => a.agent === agent);
  const pickAgent = (next: string) => {
    setAgent(next);
    const permissions = agents.find((a) => a.agent === next)?.permissions ?? [];
    if (!permissions.some((p) => p.id === permission && p.available)) setPermission(permissions.find((p) => p.isDefault && p.available)?.id ?? 'read_only');
  };
  const choices = workspaces.filter((w) => w.path);
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
    try { const done = await applyGoal(token, { proposalId: review.preview.proposalId, agent, permission, model: agent === draftAgent ? model : null, effort: agent === draftAgent ? effort : null }); onCreated(done.threadId); }
    catch (error) { setFailure(error instanceof Error ? error.message : String(error)); setCreating(false); }
  };

  const error = review.status === 'error' ? review.error : null;
  return (
    <Dialog title={t('loopx.goal.createTitle')} confirmLabel={creating ? t('loopx.goal.creating') : t('loopx.goal.createGo')}
      confirmDisabled={review.status !== 'ready' || creating} cancelDisabled={creating} className="goal-confirm" onConfirm={() => void create()} onCancel={onCancel}>
      <div className="goal-confirm-body">
        <label className="goal-confirm-field"><span>{t('loopx.goal.goal')}</span><div className="goal-confirm-objective">{text}</div></label>
        <label className="goal-confirm-field"><span>{t('loopx.goal.completion')}</span>
          <textarea rows={2} value={completion} onChange={(e) => setCompletion(e.target.value)} placeholder={t('loopx.goal.completionHint')} /></label>
        <label className="goal-confirm-field"><span>{t('loopx.goal.boundary')}</span>
          <textarea rows={2} value={boundary} onChange={(e) => setBoundary(e.target.value)} placeholder={t('loopx.goal.boundaryHint')} /></label>
        <dl className="goal-confirm-facts">
          <dt>{t('loopx.goal.agent')}</dt>
          <dd><select className="goal-confirm-select" value={agent} onChange={(e) => pickAgent(e.target.value)} aria-label={t('loopx.goal.agent')}>
            {(agents.length ? agents.filter((a) => a.available || a.agent === agent) : [{ agent, available: true, permissions: [] } as GoalAgentCapability]).map((a) =>
              <option key={a.agent} value={a.agent} disabled={!a.available} title={a.reason}>{agentLabel(a.agent)}{a.available ? '' : ` · ${a.reason ?? ''}`}</option>)}
          </select></dd>
          <dt>{t('loopx.goal.permission')}</dt>
          <dd><select className="goal-confirm-select" value={permission} onChange={(e) => setPermission(e.target.value as GoalPermission)} aria-label={t('loopx.goal.permission')}>
            {(capability?.permissions.length ? capability.permissions : [{ id: permission, available: true }]).map((p) =>
              <option key={p.id} value={p.id} disabled={!p.available}>{permissionCopy(p.id, capability).label} · {p.available ? permissionCopy(p.id, capability).hint : p.reason}</option>)}
          </select></dd>
          <dt>{t('loopx.goal.workspace')}</dt>
          <dd>{choices.length
            ? <select className="goal-confirm-select" value={cwd} title={cwd} onChange={(e) => setCwd(e.target.value)} aria-label={t('loopx.goal.workspace')}>
                {!choices.some((w) => w.path === cwd) && <option value={cwd}>{cwd}</option>}
                {choices.map((w) => <option key={w.ref} value={w.path!} title={w.path!}>{w.title}</option>)}
              </select>
            : <code>{review.status === 'ready' ? review.preview.workspace : cwd}</code>}</dd>
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
