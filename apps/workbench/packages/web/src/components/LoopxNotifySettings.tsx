import { useEffect, useState } from 'react';
import { t } from '../i18n';
import type { LoopxGoal, LoopxNotification } from '../loopx/status';
import { sortGoalTree } from '../loopx/status';
import {
  browserAlertsWanted, browserPermission, configureAutoNotify, fetchNotifyTargets, setBrowserAlertsWanted, setupGoalChannel,
  type NotifyTarget,
} from '../loopx/notify';
import { SettingsSection } from './PageNavigation';

type Bind = { phase: 'idle' } | { phase: 'busy' } | { phase: 'confirm'; target: string; preview: string } | { phase: 'error'; message: string };

function GoalRow({ token, goal, row, targets, onChanged }: {
  token: string | undefined; goal: LoopxGoal; row?: LoopxNotification; targets: NotifyTarget[]; onChanged: () => void;
}) {
  const [target, setTarget] = useState(targets[0]?.targetName ?? '');
  const [bind, setBind] = useState<Bind>({ phase: 'idle' });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!target && targets[0]) setTarget(targets[0].targetName); }, [targets, target]);
  const configured = row?.configured === true;
  const run = async (execute: boolean) => {
    if (!target) return;
    setBind({ phase: 'busy' });
    try {
      const result = await setupGoalChannel(token, goal.goalId, target, execute);
      if (!result.ok) { setBind({ phase: 'error', message: result.message ?? t('loopx.notify.bindFailed') }); return; }
      if (execute) { setBind({ phase: 'idle' }); onChanged(); } else setBind({ phase: 'confirm', target, preview: result.message ?? '' });
    } catch (e) { setBind({ phase: 'error', message: e instanceof Error ? e.message : String(e) }); }
  };
  const toggle = async (kind: 'human_gate' | 'blocked_notice', on: boolean) => {
    setBusy(true); setError('');
    try {
      const result = await configureAutoNotify(token, goal.goalId, kind, on);
      if (!result.ok) setError(result.message ?? t('loopx.notify.setupFailed')); else onChanged();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const switchLine = (kind: 'human_gate' | 'blocked_notice', label: string, on: boolean) => (
    <div className="personal-setting-line"><span>{label}</span>
      <button className="personal-switch" role="switch" aria-label={`${goal.title} · ${label}`} aria-checked={on} disabled={busy} onClick={() => void toggle(kind, !on)}><span /></button>
    </div>
  );
  return <li className="loopx-notify-row">
    <div className="loopx-notify-head">
      <strong>{goal.title}</strong>
      <span className={`loopx-notify-badge ${configured ? 'on' : ''}`}>{configured ? (row?.enabled ? t('loopx.notify.bound') : t('loopx.notify.disabled')) : t('loopx.notify.notBound')}</span>
    </div>
    {configured ? <>
      <p className="loopx-notify-note">
        {[row?.targetRef ? t('loopx.notify.group', row.targetRef) : '', t('loopx.notify.sent', row?.receiptCount ?? 0), row?.lastNotifiedAt ? t('loopx.notify.recent', new Date(row.lastNotifiedAt).toLocaleString()) : ''].filter(Boolean).join(' · ')}
      </p>
      {switchLine('human_gate', t('loopx.notify.humanGate'), row?.humanGateAutoNotify === true)}
      {switchLine('blocked_notice', t('loopx.notify.blocked'), row?.blockedNoticeAutoNotify === true)}
    </> : targets.length === 0 ? null
      : bind.phase === 'confirm' ? <div className="loopx-notify-confirm">
        <p>{t('loopx.notify.bindConfirm', goal.title, bind.target)}</p>
        {bind.preview && <p className="loopx-notify-note">{bind.preview}</p>}
        <div className="settings-actions-inline">
          <button className="primary" onClick={() => void run(true)}>{t('loopx.notify.confirmBind')}</button>
          <button className="ghost" onClick={() => setBind({ phase: 'idle' })}>{t('common.cancel')}</button>
        </div>
      </div> : <div className="loopx-notify-bind">
        <select aria-label={`${t('loopx.notify.bind')} · ${goal.title}`} value={target} onChange={e => setTarget(e.target.value)}>
          {targets.map(x => <option key={x.targetName} value={x.targetName}>{x.targetName}</option>)}
        </select>
        <button className="ghost" disabled={!target || bind.phase === 'busy'} onClick={() => void run(false)}>{t('loopx.notify.bind')}</button>
      </div>}
    {bind.phase === 'error' && <p className="personal-warning" role="alert">{bind.message}</p>}
    {error && <p className="personal-warning" role="alert">{error}</p>}
  </li>;
}

/** Settings → LoopX notifications: the browser alert, and per Goal the Lark group and its automatic notices. */
export function LoopxNotifySettings({ token, goals, notifications, failed, onChanged }: {
  token: string | undefined; goals: LoopxGoal[]; notifications: LoopxNotification[]; failed: boolean; onChanged: () => void;
}) {
  const [targets, setTargets] = useState<NotifyTarget[] | null>(null);
  const [targetsError, setTargetsError] = useState('');
  const [wanted, setWanted] = useState(browserAlertsWanted);
  const [permission, setPermission] = useState(browserPermission);
  useEffect(() => {
    let live = true;
    fetchNotifyTargets(token).then(x => { if (live) setTargets(x); }).catch(e => { if (live) { setTargets([]); setTargetsError(e instanceof Error ? e.message : String(e)); } });
    return () => { live = false; };
  }, [token]);
  const toggleBrowser = async () => {
    if (wanted) { setBrowserAlertsWanted(false); setWanted(false); return; }
    let next = browserPermission();
    if (next === 'default') next = await Notification.requestPermission();
    setPermission(next);
    if (next === 'granted') { setBrowserAlertsWanted(true); setWanted(true); }
  };
  const sorted = sortGoalTree(goals.filter(goal => goal.activationState === 'active'));
  return <SettingsSection title={t('loopx.notify.title')} hint={t('loopx.notify.hint')} className="loopx-notify">
    <div className="personal-setting-line"><span>{t('loopx.notify.browser')}
      <small className={permission === 'granted' || permission === 'default' ? '' : 'personal-warning'}>
        {permission === 'unsupported' ? t('loopx.notify.browserUnsupported') : permission === 'denied' ? t('loopx.notify.browserDenied') : t('loopx.notify.browserHint')}
      </small></span>
      <button className="personal-switch" role="switch" aria-label={t('loopx.notify.browser')} aria-checked={wanted && permission === 'granted'} disabled={permission === 'unsupported' || permission === 'denied'} onClick={() => void toggleBrowser()}><span /></button>
    </div>
    <h3 className="settings-subtitle">{t('loopx.notify.lark')}</h3>
    {failed && <p className="personal-warning" role="alert">{t('loopx.notify.coreDown')}</p>}
    {targetsError && <p className="personal-warning" role="alert">{targetsError}</p>}
    {targets === null && !failed && <p>{t('common.loading')}</p>}
    {targets !== null && !failed && sorted.length === 0 && <p className="loopx-notify-note">{t('loopx.notify.noGoals')}</p>}
    {targets !== null && !failed && !targetsError && targets.length === 0 && sorted.length > 0 && <p className="loopx-notify-note">{t('loopx.notify.noTargets')} <code>loopx goal-channel target add</code></p>}
    {targets !== null && !failed && <ul className="loopx-notify-list">
      {sorted.map(goal => <GoalRow key={goal.goalId} token={token} goal={goal} row={notifications.find(x => x.goalId === goal.goalId)} targets={targets} onChanged={onChanged} />)}
    </ul>}
  </SettingsSection>;
}
