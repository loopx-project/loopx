import { createPortal } from 'react-dom';
import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import type { Api, ExploreRecommendation, ExploreSource, ExploreSourceId, ExploreStatus, LarkSearchResult } from '../api';
import { exploreChanges, exploreCounts, exploreDraft, explorePayloads, type ExploreItem, type ExploreSnapshot } from '../personal-explore';
import { t } from '../i18n';
import { DialogSurface } from './Dialog';
import { Popover } from './Popover';
import { PushSettingsDialog } from './PushSettingsDialog';
import { IconCheck, IconChevron, IconClose, IconPlus, IconSearch } from './icons';

const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const sourceName = (source: string) => ['lark-profile', 'lark-chats', 'lark-messages', 'lark-calendar', 'calli-threads'].includes(source)
  ? t(`explore.source.${source as ExploreSourceId}`) : source;

function Row({ label, why, control, children }: { label: ReactNode; why?: ReactNode; control?: ReactNode; children?: ReactNode }) {
  return <div className="explore-row"><div className="explore-top"><div className="explore-main"><div className="explore-label">{label}</div>{why && <div className="explore-why">{why}</div>}</div>{control && <div className="explore-control">{control}</div>}</div>{children}</div>;
}
function Switch({ label, checked, disabled, onChange }: { label: string; checked: boolean; disabled?: boolean; onChange: (value: boolean) => void }) {
  return <button type="button" className="personal-switch" role="switch" aria-label={label} aria-checked={checked} disabled={disabled} onClick={() => onChange(!checked)}><span /></button>;
}
function TimePicker({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  const times = [...new Set([value, ...Array.from({ length: 96 }, (_, i) => `${String(Math.floor(i / 4)).padStart(2, '0')}:${String(i % 4 * 15).padStart(2, '0')}`)])].sort();
  return <Popover label={label} placement="below" triggerClassName="explore-value" panelClassName="explore-pop" trigger={<>{value}<IconChevron size={12} open /></>}>
    {close => <div className="pop-scroll">{times.map(time => <button key={time} type="button" className="pop-item compact" role="menuitemradio" aria-checked={time === value} onClick={() => { onChange(time); close(); }}>{time}</button>)}</div>}
  </Popover>;
}

type Candidate = ExploreRecommendation['chats'][number] & { role?: 'boss' | 'pm' | 'other' };
function Candidates({ api, kind, items, onChange }: { api: Api; kind: 'people' | 'chats'; items: Candidate[]; onChange: (items: Candidate[]) => void }) {
  const [expanded, setExpanded] = useState(false);
  const [query, setQuery] = useState<string | null>(null);
  const [hits, setHits] = useState<LarkSearchResult[] | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let live = true;
    setHits(null); setError('');
    if (!query?.trim()) return;
    const timer = window.setTimeout(() => {
      void api.searchLark(kind, query.trim()).then(found => { if (live) setHits(found); }).catch(e => { if (live) setError(errorText(e)); });
    }, 250);
    return () => { live = false; window.clearTimeout(timer); };
  }, [api, kind, query, retry]);
  const selected = items.filter(item => item.selected), candidates = items.filter(item => !item.selected);
  const shown = expanded ? candidates : candidates.slice(0, 4);
  const update = (id: string, patch: Partial<Candidate>) => onChange(items.map(item => item.id === id ? { ...item, ...patch } : item));
  const results = hits?.filter(hit => !items.some(item => item.id === hit.id && item.selected));
  const searchLabel = t(kind === 'people' ? 'explore.searchPeople' : 'explore.searchChats');
  return <>
    <div className="explore-chips">
      {selected.map(item => <Popover key={item.id} label={item.name} triggerClassName="explore-chip selected" panelClassName="explore-pop" placement="below" trigger={<><IconCheck size={13} /><span>{item.name}</span>{item.role && item.role !== 'other' && <small>{t(item.role === 'boss' ? 'explore.boss' : 'lark.role.pm')}</small>}</>}>
        {close => <><div className="explore-evidence"><strong>{item.name}</strong><p>{item.why}</p>{item.evidence.filter(line => line !== item.why).map((line, i) => <p key={i}>{line}</p>)}</div>
          {kind === 'people' && <div className="explore-segment" role="group" aria-label={t('lark.role')}>{(['boss', 'pm', 'other'] as const).map(role => <button key={role} type="button" aria-pressed={item.role === role} onClick={() => update(item.id, { role })}>{t(role === 'boss' ? 'explore.boss' : `lark.role.${role}`)}</button>)}</div>}
          <button type="button" className="pop-item compact" onClick={() => { update(item.id, { selected: false }); close(); }}>{t('explore.unfollow')}</button></>}
      </Popover>)}
      {shown.map(item => <button key={item.id} type="button" className="explore-chip" title={item.why} onClick={() => update(item.id, { selected: true })}><IconPlus size={13} />{item.name}</button>)}
      {candidates.length > shown.length && <button type="button" className="explore-chip quiet" onClick={() => setExpanded(true)}>{t('explore.more', candidates.length - shown.length)}</button>}
      {query === null ? <button type="button" className="explore-chip quiet" onClick={() => setQuery('')}><IconSearch size={13} />{t('explore.search')}</button>
        : <label className="explore-search"><IconSearch size={13} /><input autoFocus aria-label={searchLabel} placeholder={searchLabel} value={query} onChange={e => setQuery(e.target.value)} /></label>}
    </div>
    {!items.length && query === null && <p className="explore-why">{t('explore.empty')}</p>}
    {query !== null && <div className="explore-search-results" aria-live="polite">
      {!query.trim() ? <p className="explore-why">{t('explore.searchHint')}</p> : error ? <p className="panel-error" role="alert">{error} <button type="button" className="ghost" onClick={() => setRetry(n => n + 1)}>{t('common.retry')}</button></p>
        : !results ? <p className="explore-why">{t('common.loading')}</p> : !results.length ? <p className="explore-why">{t('explore.noHits')}</p>
          : <div className="explore-chips">{results.map(hit => <button key={hit.id} type="button" className="explore-chip" onClick={() => {
            if (items.some(item => item.id === hit.id)) update(hit.id, { selected: true });
            else onChange([...items, { id: hit.id, name: hit.name, selected: true, why: hit.hint || t('explore.addedSearch'), evidence: [], ...(kind === 'people' ? { role: 'other' as const } : {}) }]);
          }}><IconPlus size={13} />{hit.name}{hit.hint && <small>{hit.hint}</small>}</button>)}</div>}
    </div>}
  </>;
}

export function ExploreDialog({ api, onClose, onApplied }: { api: Api; onClose: () => void; onApplied: (draft: ExploreRecommendation, saved: ExploreSnapshot) => void }) {
  const titleId = useId();
  const [sources, setSources] = useState<ExploreSource[] | null>(null);
  const [selected, setSelected] = useState<ExploreSourceId[]>([]);
  const [status, setStatus] = useState<ExploreStatus | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [loadRetry, setLoadRetry] = useState(0);
  const [pollRetry, setPollRetry] = useState(0);
  const [snapshotRetry, setSnapshotRetry] = useState(0);
  const [snapshot, setSnapshot] = useState<ExploreSnapshot | null>(null);
  const [draft, setDraft] = useState<ExploreRecommendation | null>(null);
  const [snapshotError, setSnapshotError] = useState('');
  const [busy, setBusy] = useState(false);
  const [failures, setFailures] = useState<Partial<Record<ExploreItem, string>>>({});
  const [showDraft, setShowDraft] = useState(false);
  const [pushOpen, setPushOpen] = useState(false);
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  useEffect(() => {
    let active = true; setError('');
    void Promise.all([api.exploreSources(), api.exploreStatus()]).then(([next, current]) => {
      if (!active) return;
      setSources(next); setSelected(next.filter(source => source.available).map(source => source.id));
      if (current?.state === 'collecting' || current?.state === 'judging') { setStatus(current); setRunId(current.id); }
    }).catch(e => { if (active) setError(errorText(e)); });
    return () => { active = false; };
  }, [api, loadRetry]);

  useEffect(() => {
    if (!runId) return;
    let active = true, timer = 0;
    const poll = async () => {
      try {
        const next = await api.exploreStatus();
        if (!active) return;
        if (!next || next.id !== runId) throw new Error(t('explore.failed'));
        setStatus(next); setError('');
        if (next.state === 'collecting' || next.state === 'judging') timer = window.setTimeout(poll, 700);
        else setRunId(null);
      } catch (e) { if (active) setError(errorText(e)); }
    };
    void poll();
    return () => { active = false; window.clearTimeout(timer); };
  }, [api, runId, pollRetry]);

  const ready = status?.state === 'ready' && Boolean(status.recommendation);
  useEffect(() => {
    if (!ready || !status?.recommendation) return;
    let active = true; setSnapshotError('');
    const recommendation = status.recommendation;
    void Promise.all([api.personalSettings(), api.larkChannel(), api.briefSettings(), api.pushSettings(), api.proactiveSettings(), api.personalProfile()])
      .then(([personal, lark, brief, push, proactive, profile]) => {
        if (!active) return;
        const current = { personal, lark, brief, push, proactive: proactive.settings, profile };
        setSnapshot(current); setDraft(exploreDraft(recommendation, current));
      }).catch(e => { if (active) setSnapshotError(errorText(e)); });
    return () => { active = false; };
  }, [api, ready, status?.id, snapshotRetry]);

  const start = async () => {
    setBusy(true); setError(''); setFailures({});
    try {
      const result = await api.startExplore(selected);
      if (live.current) { setSnapshot(null); setDraft(null); setStatus({ id: result.id, state: 'collecting', steps: [], startedAt: new Date().toISOString() }); setRunId(result.id); }
    } catch (e) { if (live.current) setError(errorText(e)); }
    finally { if (live.current) setBusy(false); }
  };
  const stop = async () => {
    setBusy(true); setError('');
    try { await api.cancelExplore(); const next = await api.exploreStatus(); if (live.current) { setStatus(next); setRunId(null); } }
    catch (e) { if (live.current) setError(errorText(e)); }
    finally { if (live.current) setBusy(false); }
  };
  const apply = async () => {
    if (!draft || !snapshot) return;
    setBusy(true); setFailures({});
    let current = snapshot;
    const failed: Partial<Record<ExploreItem, string>> = {};
    for (const key of exploreChanges(draft, current)) {
      const next = explorePayloads(draft, current);
      try {
        switch (key) {
          case 'personal': current = { ...current, personal: await api.savePersonalSettings(next.personal) }; break;
          case 'lark': current = { ...current, lark: await api.saveLarkChannel(next.lark) }; break;
          case 'brief': current = { ...current, brief: await api.saveBriefSettings(next.brief) }; break;
          case 'push': current = { ...current, push: await api.savePushSettings(next.push) }; break;
          case 'proactive': current = { ...current, proactive: await api.saveProactiveSettings(next.proactive) }; break;
          case 'profile': current = { ...current, profile: await api.savePersonalProfile(next.profile.text, next.profile.rev) }; break;
        }
      } catch (e) { failed[key] = errorText(e); }
    }
    if (!live.current) return;
    setSnapshot(current); setFailures(failed); setBusy(false);
    if (!Object.keys(failed).length) onApplied(draft, current);
  };
  const closePush = () => {
    setPushOpen(false);
    void api.pushSettings().then(push => {
      if (!live.current) return;
      const available = Boolean(push.recipient.trim());
      setSnapshot(old => old && { ...old, push });
      setDraft(old => old && { ...old, push: { ...old.push, available, enabled: available && push.enabled, why: available ? '' : old.push.why } });
    }).catch(e => setError(errorText(e)));
  };
  const working = Boolean(runId);
  const counts = draft && exploreCounts(draft);
  const changes = draft && snapshot ? exploreChanges(draft, snapshot) : [];
  return createPortal(<DialogSurface className="explore-dialog" labelledBy={titleId} onClose={onClose} busy={busy}>
    <header className="explore-head"><div><h2 id={titleId}>{t(ready ? 'explore.checklist' : status ? 'explore.looking' : 'explore.sources')}</h2><p>{t(ready ? 'explore.checkHint' : 'explore.readonly')}</p></div><button type="button" className="icon-btn" disabled={busy} aria-label={t('common.close')} onClick={onClose}><IconClose size={18} /></button></header>
    <div className="explore-body">
      {!status && (!sources ? !error && <p role="status">{t('common.loading')}</p> : sources.map(source => <div key={source.id} className={source.available ? '' : 'explore-unavailable'}>
        <Row label={sourceName(source.id)} why={<>{t(`explore.source.${source.id}.hint`)}{source.reason && <span className="explore-source-reason">{source.reason}</span>}</>} control={<Switch label={sourceName(source.id)} checked={selected.includes(source.id)} disabled={!source.available || busy} onChange={on => setSelected(old => on ? [...old, source.id] : old.filter(id => id !== source.id))} />} />
      </div>))}
      {status && !ready && <div className="explore-steps" aria-live="polite">
        {status.steps.filter(step => step.state !== 'pending').map((step, i) => <div key={i} className={`explore-step ${step.state}`}>
          {step.state === 'running' ? <span className="spinner" /> : step.state === 'done' ? <IconCheck size={15} /> : <span className="explore-step-mark" />}
          <div><p>{step.line || sourceName(step.source)}</p>{step.read && <small>{step.read}</small>}{step.reason && <p className="explore-source-reason">{step.reason}</p>}</div>
        </div>)}
        {status.state === 'judging' && <p className="explore-why" role="status">{t('explore.judging')}</p>}
        {status.state === 'cancelled' && <p>{t('explore.cancelled')}</p>}
        {status.state === 'failed' && <p className="panel-error" role="alert">{status.error || t('explore.failed')}</p>}
      </div>}
      {ready && !draft && (snapshotError ? <p className="panel-error" role="alert">{t('explore.loadFailed')} {snapshotError} <button className="ghost" onClick={() => setSnapshotRetry(n => n + 1)}>{t('common.retry')}</button></p> : <p role="status">{t('common.loading')}</p>)}
      {draft && snapshot && <fieldset className="explore-fields" disabled={busy}>
        {status?.model === 'fallback' && <p className="explore-fallback">{t('explore.fallback')}</p>}
        <h3>{t('explore.group.assistant')}</h3>
        <Row label={t('personal.agent')} why={!draft.agent.options.length ? t('onboard.noneInstalled') : draft.agent.options.find(option => option.agent === draft.agent.agent && option.model === draft.agent.model)?.why}
          control={<Popover label={t('personal.agent')} disabled={!draft.agent.options.length} placement="below" panelClassName="explore-pop" triggerClassName="explore-value" trigger={<>{draft.agent.options.find(option => option.agent === draft.agent.agent && option.model === draft.agent.model)?.label || draft.agent.agent || t('personal.agent')}<IconChevron size={12} open /></>}>
            {close => <div className="pop-scroll">{draft.agent.options.map((option, i) => <button key={i} type="button" className="pop-item explore-option" role="menuitemradio" aria-checked={option.agent === draft.agent.agent && option.model === draft.agent.model} onClick={() => { setDraft({ ...draft, agent: { ...draft.agent, agent: option.agent, model: option.model } }); close(); }}><span>{option.label}{option.recommended && <em>{t('explore.recommended')}</em>}</span><small>{option.why}</small></button>)}</div>}
          </Popover>} />
        <Row label={t('explore.profile')} why={draft.profile.replace ? draft.profile.why : t('explore.keepProfile')} control={<button type="button" className="explore-value" aria-expanded={showDraft} onClick={() => setShowDraft(on => !on)}>{t(showDraft ? 'explore.fold' : 'explore.draft')}<IconChevron size={12} open={showDraft} /></button>}>
          {showDraft && <><textarea className="explore-draft" aria-label={t('explore.profile')} value={draft.profile.draft} onChange={e => setDraft({ ...draft, profile: { ...draft.profile, draft: e.target.value } })} />
            <Row label={t('explore.replaceProfile')} control={<Switch label={t('explore.replaceProfile')} checked={draft.profile.replace} onChange={replace => setDraft({ ...draft, profile: { ...draft.profile, replace } })} />} /></>}
        </Row>
        <h3>{t('explore.group.follow')}</h3>
        <Row label={<>{t('explore.people')}<span className="explore-count">{counts?.people}</span></>}><Candidates api={api} kind="people" items={draft.people} onChange={items => setDraft({ ...draft, people: items.map(item => ({ ...item, role: item.role ?? 'other' })) })} /></Row>
        <Row label={<>{t('explore.chats')}<span className="explore-count">{counts?.chats}</span></>}><Candidates api={api} kind="chats" items={draft.chats} onChange={chats => setDraft({ ...draft, chats })} /></Row>
        <Row label={t('explore.meetings')} why={<>{draft.meetings.why}{draft.meetings.examples.length > 0 && <span className="explore-source-reason">{draft.meetings.examples.join(' · ')}</span>}</>} control={<Switch label={t('explore.meetings')} checked={draft.meetings.on} onChange={on => setDraft({ ...draft, meetings: { ...draft.meetings, on } })} />} />
        <h3>{t('explore.group.rhythm')}</h3>
        <Row label={t('explore.interval')} why={draft.intervalMin.why}><div className="explore-segment" role="group" aria-label={t('explore.interval')}>{([15, 30, 60, 120] as const).map(value => <button key={value} type="button" aria-pressed={draft.intervalMin.value === value} onClick={() => setDraft({ ...draft, intervalMin: { ...draft.intervalMin, value } })}>{t('explore.minutes', value)}</button>)}</div></Row>
        <Row label={t('explore.brief')} why={draft.brief.why} control={<Switch label={t('explore.brief')} checked={draft.brief.enabled} onChange={enabled => setDraft({ ...draft, brief: { ...draft.brief, enabled } })} />}>
          {draft.brief.enabled && <div className="explore-times">{(['morning', 'evening'] as const).map(key => <label key={key}><span>{t(`explore.${key}`)}</span><TimePicker label={t(`explore.${key}`)} value={draft.brief[key]} onChange={time => setDraft({ ...draft, brief: { ...draft.brief, [key]: time } })} /></label>)}</div>}
        </Row>
        <Row label={t('explore.push')} why={draft.push.available ? draft.push.why : <button type="button" className="explore-link" onClick={() => setPushOpen(true)}>{draft.push.why || t('explore.pushSettings')}<IconChevron size={12} /></button>} control={<Switch label={t('explore.push')} checked={draft.push.enabled} disabled={!draft.push.available} onChange={enabled => setDraft({ ...draft, push: { ...draft.push, enabled } })} />} />
        <Row label={t('explore.proactive')} why={draft.proactive.why} control={<div className="explore-segment" role="group" aria-label={t('explore.proactive')}>{(['off', 'observe', 'auto'] as const).map(mode => <button key={mode} type="button" aria-pressed={draft.proactive.mode === mode} onClick={() => setDraft({ ...draft, proactive: { ...draft.proactive, mode } })}>{t(`proactive.mode.${mode}`)}</button>)}</div>} />
      </fieldset>}
      {error && <p className="panel-error" role="alert">{error} <button type="button" className="ghost" onClick={() => working ? setPollRetry(n => n + 1) : setLoadRetry(n => n + 1)}>{t('common.retry')}</button></p>}
    </div>
    <footer className="explore-foot">
      {Object.keys(failures).length > 0 && <div className="explore-failures" role="alert"><p>{t('explore.saveFailed')}</p>{(Object.entries(failures) as [ExploreItem, string][]).map(([key, message]) => <p key={key}><strong>{t(`explore.item.${key}`)}</strong> · {message}</p>)}</div>}
      <div className="explore-actions"><small>{draft && t('explore.changes', changes.length)}</small><button type="button" className="ghost" disabled={busy} onClick={onClose}>{t('explore.later')}</button>
        {working ? <button type="button" className="primary" disabled={busy} onClick={() => void stop()}>{t('explore.stop')}</button>
          : ready ? <button type="button" className="primary" disabled={busy || !draft?.agent.agent || !snapshot} onClick={() => void apply()}>{t(busy ? 'explore.saving' : Object.keys(failures).length ? 'explore.retrySave' : 'explore.apply')}</button>
            : <button type="button" className="primary" disabled={busy || !selected.length} onClick={() => void start()}>{t('explore.start')}</button>}
      </div>
    </footer>
    {pushOpen && <PushSettingsDialog api={api} onClose={closePush} />}
  </DialogSurface>, document.body);
}
