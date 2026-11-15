import { useEffect, useState } from 'react';
import type { AgentInfo, Api, AutoCompactSettings, AutoHandoverView, HandoverSettings, HandoverTarget, ModelOption, Thread } from '../api';
import { t } from '../i18n';
import { SettingsSection, usePageDraft } from './PageNavigation';
import { Popover } from './Popover';
import { AgentPicker } from './Pickers';
import { AgentMark, IconChevron, IconFork, IconGrip, IconMore } from './icons';

export function AutoHandoverChoice({ api, thread }: { api: Api; thread: Thread }) {
  const [value, setValue] = useState<AutoHandoverView | undefined>(thread.autoHandover);
  const [error, setError] = useState('');
  useEffect(() => { setValue(thread.autoHandover); }, [thread.autoHandover]);
  if (api.demo) return null;
  return <div className="auto-choice">
    <span>{t('auto.title')}</span>
    <Popover triggerClassName="context-btn" label={t('auto.title')} trigger={<>{value ? t(value.enabled ? 'auto.on' : 'auto.off') : t('common.loading')} · {value && t(`auto.source.${value.source}`)}</>}>
      {close => <div role="menu">{([null, true, false] as const).map(enabled => <button type="button" className="pop-item compact" role="menuitemradio" aria-checked={value?.override === enabled} key={String(enabled)} onClick={() => {
        void api.setAutoHandover(thread.id, enabled).then(v => { setValue(v); setError(''); close(); }).catch(e => setError(String(e)));
      }}>{t(enabled === null ? 'auto.inherit' : enabled ? 'auto.on' : 'auto.off')}</button>)}</div>}
    </Popover>
    {error && <span role="alert" className="panel-error">{error}</span>}
  </div>;
}

export function AutoHandoverNotice({ api, thread, agents }: { api: Api; thread: Thread; agents: AgentInfo[] }) {
  const state = thread.autoHandover;
  const [now, setNow] = useState(Date.now());
  const [error, setError] = useState('');
  useEffect(() => {
    if (state?.state !== 'countdown') return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(timer);
  }, [state?.state, state?.deadline]);
  if (!state?.state || state.state === 'checking') return null;
  const target = state.target;
  const label = target ? [agents.find(a => a.id === target.agent)?.label ?? target.agent, target.model].filter(Boolean).join(' · ') : '';
  const danger = state.state === 'failed' || state.state === 'stopped';
  const busy = state.state === 'preparing' || state.state === 'countdown';
  // A circle for work under way; once the move has begun it can no longer be called off.
  const calloff = busy && (state.state === 'countdown' || !state.step || state.step === 'summary');
  const text = state.state === 'countdown' ? t('auto.countdown', label, Math.max(0, Math.ceil((Date.parse(state.deadline!) - now) / 1000)))
    : state.state === 'preparing' ? t(state.step ? `auto.step.${state.step}` : 'auto.preparing') : state.state === 'stopped' ? (label ? t('auto.targetGone', label) : t('auto.stopped')) : t('auto.failed');
  return <div className={`auto-notice${danger ? ' is-danger' : ''}`} role={danger ? 'alert' : 'status'}>
    <span className="rule" />
    {busy ? <span className="spinner" aria-hidden="true" /> : <IconFork size={14} />}
    <span>{text}
      {state.state === 'preparing' && label && <small>{label}</small>}
      {state.error && <small>{state.error}</small>}{error && <small role="alert">{error}</small>}</span>
    {calloff && <button onClick={() => { void api.cancelAutoHandover(thread.id).catch(e => setError(String(e))); }}>{t('auto.cancel')}</button>}
    <span className="rule" />
  </div>;
}

const targetLabel = (agents: AgentInfo[], target: HandoverTarget) => [agents.find(a => a.id === target.agent)?.label ?? target.agent, target.model].filter(Boolean).join(' · ');

/** An ordered list of agents, reordered by drag or from the row's menu. */
function TargetList({ agents, list, label, onChange }: { agents: AgentInfo[]; list: HandoverTarget[]; label: string; onChange: (next: HandoverTarget[]) => void }) {
  const [dragged, setDragged] = useState<number | null>(null);
  const move = (from: number, to: number) => {
    if (to < 0 || to >= list.length) return;
    const next = [...list];
    next.splice(to, 0, next.splice(from, 1)[0]); onChange(next);
  };
  return <ol aria-label={label}>{list.map((target, i) => <li key={`${target.agent}:${target.model}`} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); if (dragged !== null) move(dragged, i); setDragged(null); }}>
    <span className="auto-order-grip" draggable onDragStart={() => setDragged(i)} onDragEnd={() => setDragged(null)} title={t('auto.drag')}><IconGrip size={16} /></span>
    {agents.find(a => a.id === target.agent)?.icon && <AgentMark path={agents.find(a => a.id === target.agent)!.icon!} size={16} />}
    <span className="auto-order-name">{targetLabel(agents, target)}</span>
    <Popover triggerClassName="icon-btn auto-order-more" panelClassName="auto-order-menu" label={t('auto.targetActions', targetLabel(agents, target))} triggerLabel={t('auto.targetActions', targetLabel(agents, target))} trigger={<IconMore size={16} />} align="end" placement="below">
      {close => <div role="menu">
        <button className="pop-item compact" role="menuitem" disabled={!i} onClick={() => { move(i, i - 1); close(); }}>{t('auto.up')}</button>
        <button className="pop-item compact" role="menuitem" disabled={i === list.length - 1} onClick={() => { move(i, i + 1); close(); }}>{t('auto.down')}</button>
        <button className="pop-item compact" role="menuitem" onClick={() => { onChange(list.filter((_, n) => n !== i)); close(); }}>{t('auto.remove')}</button>
      </div>}
    </Popover>
  </li>)}</ol>;
}

function AddTarget({ api, agents, addLabel, add }: { api: Api; agents: AgentInfo[]; addLabel: string; add: (target: HandoverTarget) => void }) {
  const [agent, setAgent] = useState(agents.find(a => !a.hidden)?.id ?? '');
  const [models, setModels] = useState<ModelOption[] | null>(null);
  const [model, setModel] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true; setModels(null); setModel(''); setError('');
    void api.models(agent).then(m => { if (alive) setModels(m); }).catch(e => { if (alive) setError(String(e)); });
    return () => { alive = false; };
  }, [api, agent]);
  return <div className="auto-add">
    <AgentPicker api={api} agents={agents} value={agent} onChange={setAgent} />
    <Popover triggerClassName="context-btn" label={t('auto.modelDefault')} trigger={models?.find(m => m.id === model)?.label ?? t('auto.modelDefault')}>
      {close => <div role="menu">
        <button className="pop-item compact" role="menuitemradio" aria-checked={!model} onClick={() => { setModel(''); close(); }}>{t('auto.modelDefault')}</button>
        {models?.map(m => <button className="pop-item compact" role="menuitemradio" aria-checked={model === m.id} key={m.id} onClick={() => { setModel(m.id); close(); }}>{m.label}</button>)}
        {!models && !error && <p>{t('common.loading')}</p>}{error && <p role="alert">{error}</p>}
      </div>}
    </Popover>
    <button className="ghost" onClick={() => add({ agent, ...(model ? { model } : {}) })}>{addLabel}</button>
  </div>;
}

export function HandoverSettingsDialog({ api, agents, onClose }: { api: Api; agents: AgentInfo[]; onClose: () => void }) {
  const [value, setValue] = useState<HandoverSettings | null>(null);
  const [compact, setCompact] = useState<AutoCompactSettings | null>(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [opened, setOpened] = useState('');
  const [saved, setSaved] = useState('');
  const [savedNotice, setSavedNotice] = useState(false);
  const [savedCompact, setSavedCompact] = useState('');
  const [compressOpen, setCompressOpen] = useState(false);
  const dirty = value !== null && JSON.stringify(value) !== saved || compact !== null && JSON.stringify(compact) !== savedCompact;
  usePageDraft(dirty, saving);
  const [dragged, setDragged] = useState<number | null>(null);
  const load = () => {
    setError('');
    void api.handoverSettings().then(next => { setValue(next); setSaved(JSON.stringify(next)); }).catch(e => setError(String(e)));
    // Older daemons have no compression section.
    void api.autoCompactSettings().then(({ settings }) => { setCompact(settings); setSavedCompact(JSON.stringify(settings)); }).catch(() => setCompact(null));
  };
  useEffect(load, [api]);
  const setOrder = (id: string, list: HandoverTarget[]) => setValue(v => v && ({ ...v, orders: { ...v.orders, [id]: list } }));
  const label = (target: HandoverTarget) => targetLabel(agents, target);
  const save = async () => {
    if (!value || saving) return;
    setSaving(true); setError(''); setSavedNotice(false);
    try {
      if (compact) {
        const nextCompact = await api.saveAutoCompactSettings(compact);
        setCompact(nextCompact); setSavedCompact(JSON.stringify(nextCompact));
      }
      const next = await api.saveHandoverSettings(value);
      setValue(next); setSaved(JSON.stringify(next)); setSavedNotice(true);
    } catch (e) { setError(String(e)); } finally { setSaving(false); }
  };
  const invalidCompact = compact?.enabled && (!Number.isFinite(compact.threshold) || compact.threshold < 50 || compact.threshold > 98 || !!value && compact.threshold >= value.threshold);
  return <SettingsSection busy={saving} title={t('auto.title')} className="auto-settings" actions={<>
    <span role="status">{savedNotice && !dirty ? t('settings.saved') : ''}</span>
    <button className="primary" disabled={!value || saving || !Number.isFinite(value.threshold) || value.threshold < 1 || value.threshold > 99 || !!invalidCompact} onClick={() => void save()}>{t(saving ? 'general.saving' : 'auto.save')}</button>
  </>}>
    {error && <p className="panel-error" role="alert">{error} {!value && <button onClick={load}>{t('common.retry')}</button>}</p>}
    {!value && !error && <p>{t('common.loading')}</p>}
    {value && <>
      <div className="auto-setting-line"><span>{t('auto.global')}</span><button aria-label={t('auto.global')} className="auto-toggle" role="switch" aria-checked={value.enabled} onClick={() => setValue({ ...value, enabled: !value.enabled })}>{t(value.enabled ? 'auto.on' : 'auto.off')}</button></div>
      <div className="auto-setting-line"><span id="auto-method">{t('auto.method')}</span><div className="chip-row" role="radiogroup" aria-labelledby="auto-method">
        {(['resume', 'summary'] as const).map(m => <button key={m} type="button" className={`chip${(value.method ?? 'resume') === m ? ' on' : ''}`} role="radio" aria-checked={(value.method ?? 'resume') === m} onClick={() => setValue({ ...value, method: m })}>{t(m === 'resume' ? 'auto.methodResume' : 'auto.methodSummary')}</button>)}
      </div></div>
      <p className="auto-setting-hint">{t('auto.methodHint')}</p>
      <label className="auto-setting-line">{t('auto.threshold')}<input inputMode="numeric" value={value.threshold} onChange={e => setValue({ ...value, threshold: Number(e.target.value) })} /></label>
      {compact && <>
        <div className="auto-setting-line"><span>{t('compact.enable')}</span><button aria-label={t('compact.enable')} className="auto-toggle" role="switch" aria-checked={compact.enabled} onClick={() => setCompact({ ...compact, enabled: !compact.enabled })}>{t(compact.enabled ? 'auto.on' : 'auto.off')}</button></div>
        {compact.enabled && <>
          <label className="auto-setting-line">{t('compact.threshold')}<input inputMode="numeric" value={compact.threshold} onChange={e => setCompact({ ...compact, threshold: Number(e.target.value) })} disabled={!value.enabled} /></label>
        </>}
        <div className="auto-orders single"><section>
          <button className="auto-order-head" aria-expanded={compressOpen} onClick={() => setCompressOpen(!compressOpen)}>
            <strong>{t('compact.agents')}</strong><span>{compact.agents.map(label).join(' → ') || t('compact.none')}</span><IconChevron size={14} open={compressOpen} />
          </button>
          {compressOpen && <div className="auto-order-edit">
            <p className="hint">{t('compact.hint')}</p>
            <TargetList agents={agents} list={compact.agents} label={t('compact.order')} onChange={next => setCompact({ ...compact, agents: next })} />
            <AddTarget api={api} agents={agents.filter(a => !a.hidden)} addLabel={t('compact.add')} add={target => { if (!compact.agents.some(x => x.agent === target.agent && x.model === target.model)) setCompact({ ...compact, agents: [...compact.agents, target] }); }} />
          </div>}
        </section></div>
      </>}
      <div className="auto-setting-line"><span>{t('auto.resume')}</span><button aria-label={t('auto.resume')} className="auto-toggle" role="switch" aria-checked={value.resume === true} onClick={() => setValue({ ...value, resume: value.resume !== true })}>{t(value.resume === true ? 'auto.on' : 'auto.off')}</button></div>
      <p className="auto-setting-hint">{t('auto.resumeHint')}</p>
      <div className="auto-orders">{agents.map(agent => {
        const order = value.orders[agent.id];
        return <section key={agent.id}>
          <button className="auto-order-head" aria-expanded={opened === agent.id} onClick={() => setOpened(opened === agent.id ? '' : agent.id)}>
            <strong>{agent.label}</strong><span>{order ? order.map(label).join(' → ') || t('auto.noTargets') : t('auto.defaultOrder')}</span><IconChevron size={14} open={opened === agent.id} />
          </button>
          {opened === agent.id && <div className="auto-order-edit">
            <TargetList agents={agents} list={order ?? []} label={t('auto.order')} onChange={next => setOrder(agent.id, next)} />
            <AddTarget api={api} agents={agents} addLabel={t('auto.add')} add={target => { if (!order?.some(t => t.agent === target.agent && t.model === target.model)) setOrder(agent.id, [...(order ?? []), target]); }} />
            {order && <button className="text-btn" onClick={() => { const orders = { ...value.orders }; delete orders[agent.id]; setValue({ ...value, orders }); }}>{t('auto.reset')}</button>}
          </div>}
        </section>;
      })}</div>
    </>}
  </SettingsSection>;
}
