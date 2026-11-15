import { useEffect, useRef, useState } from 'react';
import loopxIcon from '../assets/loo-icon-blue.svg';
import '../loopx/loopx.css';
import { applyGoal, defaultGoalPermission, fetchGoalBar, lx, LoopxError, PERMISSION_COPY, previewGoal, STATUS_WORDS,
  type GoalAgentCapability, type GoalBarRead, type GoalBinding, type GoalPermission, type GoalPreview } from '../loopx/launch';
import { Dialog } from './Dialog';
import { IconCheck, IconHand, IconShield } from './icons';
import { Popover } from './Popover';

/** The LoopX mark next to the voice button: turns a new conversation into a Goal conversation. */
export function LoopxToggle({ active, onToggle, disabledReason }: { active: boolean; onToggle: () => void; disabledReason?: string }) {
  const title = disabledReason ?? (active ? lx('取消 LoopX 会话', 'Back to an ordinary conversation') : lx('交给 LoopX 持续跑', 'Hand to LoopX to keep going'));
  return (
    <button type="button" className={`tool-btn composer-loopx${active ? ' active' : ''}`} disabled={Boolean(disabledReason)} onClick={onToggle} title={title} aria-label={title} aria-pressed={active}>
      <img src={loopxIcon} alt="" width={18} height={18} />
      {active && <span className="composer-loopx-label">LoopX</span>}
    </button>
  );
}

/** The two levels of a Goal conversation, in the place the ordinary conversation has its three. */
export function GoalPermissionPicker({ capability, value, onChange }: { capability: GoalAgentCapability | undefined; value: GoalPermission; onChange: (p: GoalPermission) => void }) {
  const Icon = value === 'workspace_auto' ? IconShield : IconHand;
  return (
    <Popover label={lx('权限', 'Permission')} triggerClassName="tool-btn icon-only composer-permission composer-goal-permission" triggerLabel={`${lx('权限', 'Permission')}：${PERMISSION_COPY[value].label}`} triggerTitle={PERMISSION_COPY[value].hint}
      trigger={<><Icon size={16} /><span className="composer-goal-permission-name">{PERMISSION_COPY[value].label}</span></>}>
      {(close) => <>
        <div className="pop-title">{lx('LoopX 会话权限 · 由沙箱强制', 'Goal permission · enforced by the sandbox')}</div>
        {(capability?.permissions ?? []).map((p) => {
          const ItemIcon = p.id === 'workspace_auto' ? IconShield : IconHand;
          return (
            <button key={p.id} type="button" role="menuitemradio" aria-checked={p.id === value} aria-disabled={!p.available} disabled={!p.available}
              className="pop-item" title={p.reason} onClick={() => { onChange(p.id); close(); }}>
              <span className="pop-item-icon"><ItemIcon size={18} /></span>
              <span className="pop-item-text">
                <span className="pop-item-label">{PERMISSION_COPY[p.id].label}</span>
                <span className="pop-item-hint">{p.available ? PERMISSION_COPY[p.id].hint : p.reason}</span>
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
      <img src={loopxIcon} alt="" width={16} height={16} />
      <span className="goal-bar-title" title={binding.goalId}>{state && state.title !== binding.goalId ? state.title : title || binding.goalId}</span>
      {state ? <span className="goal-bar-status">{STATUS_WORDS[state.status] ?? state.status}</span>
        : <span className="goal-bar-status warn">{read ? lx('Goal 状态暂时读不到', 'Goal state unavailable') : lx('读取中…', 'Reading…')}</span>}
      {state && <span className="goal-bar-todos" title={lx('未完成 / 总数', 'open / total')}>{lx('Agent 待办', 'Agent todos')} {open(state.agentTodos)} · {lx('你的待办', 'Your todos')} {open(state.userTodos)}</span>}
      <span className="goal-bar-permission" title={PERMISSION_COPY[binding.permission].hint}>{PERMISSION_COPY[binding.permission].label}</span>
      <button type="button" className="goal-bar-refresh" disabled={busy} onClick={() => void load(true)} title={read?.error ?? lx('刷新', 'Refresh')} aria-label={lx('刷新 Goal 状态', 'Refresh Goal state')}>{busy ? '…' : '↻'}</button>
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
    <Dialog title={lx('创建 LoopX Goal', 'Create a LoopX Goal')} confirmLabel={creating ? lx('创建中…', 'Creating…') : lx('创建 Goal 并开始', 'Create Goal and start')}
      confirmDisabled={review.status !== 'ready' || creating} className="goal-confirm" onConfirm={() => void create()} onCancel={onCancel}>
      <div className="goal-confirm-body">
        <label className="goal-confirm-field"><span>{lx('目标', 'Goal')}</span><div className="goal-confirm-objective">{text}</div></label>
        <label className="goal-confirm-field"><span>{lx('完成条件（建议写，能检查最好）', 'Completion (checkable is best)')}</span>
          <textarea rows={2} value={completion} onChange={(e) => setCompletion(e.target.value)} placeholder={lx('比如：测试通过、文件存在', 'e.g. tests pass, file exists')} /></label>
        <label className="goal-confirm-field"><span>{lx('执行边界（可选）', 'Boundary (optional)')}</span>
          <textarea rows={2} value={boundary} onChange={(e) => setBoundary(e.target.value)} placeholder={lx('比如：只改 docs/ 目录', 'e.g. only touch docs/')} /></label>
        <dl className="goal-confirm-facts">
          <dt>{lx('Agent', 'Agent')}</dt><dd>{agentLabel}</dd>
          <dt>{lx('权限', 'Permission')}</dt><dd>{PERMISSION_COPY[permission].label} · {PERMISSION_COPY[permission].hint}</dd>
          <dt>{lx('工作目录', 'Workspace')}</dt><dd><code>{review.status === 'ready' ? review.preview.workspace : cwd}</code></dd>
        </dl>
        {review.status === 'loading' && <p className="goal-confirm-note" role="status">{lx('LoopX 核心正在检查这份 Goal…', 'The LoopX core is reviewing this Goal…')}</p>}
        {review.status === 'ready' && <p className="goal-confirm-note ok" role="status">{lx('核心已检查：', 'Reviewed by the core: ')}{review.preview.title}</p>}
        {error && <div className="goal-confirm-error" role="alert">
          <p>{error.message}</p>
          {error instanceof LoopxError && error.code === 'workspace_not_granted' && error.candidates?.length ? <>
            <p>{lx('核心目前只允许在这些目录里创建 Goal（要加目录，用 --scan-path 重启核心）：', 'The core only lets Goals be created in these directories (restart it with --scan-path to add one):')}</p>
            <ul>{error.candidates.map((c) => <li key={c.path}><code>{c.path}</code></li>)}</ul></> : null}
          {error instanceof LoopxError && error.code === 'loopx_core_unavailable' && <p>{lx('LoopX 核心没有响应。普通会话不受影响。', 'The LoopX core is not responding. Ordinary conversations are not affected.')}</p>}
        </div>}
        {failure && <div className="goal-confirm-error" role="alert"><p>{failure}</p></div>}
      </div>
    </Dialog>
  );
}
