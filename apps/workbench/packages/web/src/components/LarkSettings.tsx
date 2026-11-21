import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { ApiError, type Api, type LarkChannelSettings, type LarkChannelStatus, type LarkSearchResult } from '../api';
import { t } from '../i18n';
import { LARK_INTERVALS, LARK_ROLES, larkHealth, meetingEventCoverage, realtimeLine, withPostMeetingEvents } from '../lark';
import { timeAgo } from '../sessions';
import { IconChevron, IconClose, IconMinus, IconPlus, IconSearch } from './icons';
import { Popover } from './Popover';

/** The channel and how it is doing, for the settings section and the line on the desk. */
export function useLark(api: Api) {
  const [channel, setChannel] = useState<LarkChannelSettings | null>(null);
  const [status, setStatus] = useState<LarkChannelStatus | null>(null);
  const [loadError, setLoadError] = useState('');
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState('');
  const asked = useRef(0);
  const ran = useRef(0);
  const load = useCallback(() => {
    const ticket = ++asked.current;
    setLoadError(''); setLoading(true);
    void Promise.all([api.larkChannel(), api.larkStatus()]).then(([nextChannel, nextStatus]) => {
      if (ticket === asked.current) { setChannel(nextChannel); setStatus(nextStatus); }
    }).catch(e => {
      if (ticket === asked.current) setLoadError(e instanceof Error ? e.message : String(e));
    }).finally(() => { if (ticket === asked.current) setLoading(false); });
  }, [api]);
  useEffect(() => {
    setChannel(null); setStatus(null); setRunError(''); setRunning(false);
    load();
    return () => { ++asked.current; ++ran.current; };
  }, [load]);
  const run = useCallback(() => {
    if (running) return;
    const ticket = ++ran.current;
    setRunning(true); setRunError('');
    void api.runLark().then(next => { if (ticket === ran.current) setStatus(next); }).catch(e => {
      if (ticket === ran.current) setRunError(e instanceof Error ? e.message : String(e));
    }).finally(() => { if (ticket === ran.current) setRunning(false); });
  }, [api, running]);
  return { channel, setChannel, status, loading, loadError, load, run, running, runError };
}

function LarkSearch({ api, kind, onPick }: { api: Api; kind: 'people' | 'chats'; onPick: (hit: LarkSearchResult) => void }) {
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<LarkSearchResult[] | null>(null);
  const [error, setError] = useState('');
  const asked = useRef(0);
  useEffect(() => {
    const words = query.trim();
    const ticket = ++asked.current;
    setError('');
    if (!words) { setHits(null); return; }
    const timer = window.setTimeout(() => {
      void api.searchLark(kind, words).then(found => { if (ticket === asked.current) setHits(found); }).catch(e => { if (ticket === asked.current) { setHits(null); setError(e instanceof ApiError && e.status === 409 ? t('lark.noLogin') : e instanceof Error ? e.message : String(e)); } });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [api, kind, query]);
  return <>
    <label className="pop-search"><IconSearch size={15} /><input autoFocus aria-label={t(kind === 'people' ? 'lark.searchPeople' : 'lark.searchChats')} placeholder={t(kind === 'people' ? 'lark.searchPeople' : 'lark.searchChats')} value={query} onChange={e => setQuery(e.target.value)} /></label>
    <div className="pop-scroll">
      {hits?.map(hit => <button key={hit.id} className="pop-item compact lark-hit" onClick={() => onPick(hit)}><span>{hit.name}</span>{hit.hint && <small>{hit.hint}</small>}</button>)}
      {hits?.length === 0 && <p className="lark-note">{t('lark.noHits')}</p>}
      {error && <p className="panel-error" role="alert">{error}</p>}
    </div>
  </>;
}

/** Whether events arrive as they happen, and the followed topics that could not be read. */
function RealtimeStatus({ status }: { status: LarkChannelStatus | null }) {
  const [open, setOpen] = useState(false);
  const line = realtimeLine(status?.realtime);
  const errors = status?.topicErrors ?? [];
  if (!line && !errors.length) return null;
  return <div className="lark-realtime">
    {line && <p className={line.warn ? 'personal-warning' : 'lark-live'}>{line.text}</p>}
    {errors.length > 0 && <button className="desk-subscriptions-toggle personal-warning" aria-expanded={open} onClick={() => setOpen(on => !on)}>{t('lark.topicErrors', errors.length)}<IconChevron size={13} open={open} /></button>}
    {open && <ul>{errors.map(item => <li key={item.ref}><code>{item.ref}</code><code>{item.error}</code></li>)}</ul>}
  </div>;
}

/** The Lark section of the private settings. Changes wait for the page's Save; only reading now happens at once. */
export function LarkSettings({ api, draft, onDraft, onLoaded }: { api: Api; draft: LarkChannelSettings | null; onDraft: (next: LarkChannelSettings) => void; onLoaded?: (value: LarkChannelSettings) => void }) {
  const { channel: savedChannel, status, loadError, load, run, running, runError } = useLark(api);
  // Preserve local edits while the source status is refreshed.
  const [localDraft, setLocalDraft] = useState(draft);
  useEffect(() => { setLocalDraft(draft); }, [api, draft]);
  useEffect(() => { if (savedChannel) onLoaded?.(savedChannel); }, [savedChannel, onLoaded]);
  const meetingHintId = useId();
  const channel = localDraft ?? savedChannel;
  if (loadError) return <div className="personal-setting-line"><span>{t('lark.name')}<small className="personal-warning" role="alert">{t('lark.loadFailed')}</small></span><button className="ghost" onClick={load}>{t('common.retry')}</button></div>;
  if (!channel) return <div className="personal-setting-line"><span>{t('lark.name')}<small role="status">{t('common.loading')}</small></span></div>;
  const save = (next: LarkChannelSettings) => {
    setLocalDraft(next);
    onDraft(next);
  };
  const health = larkHealth(channel, status);
  const coverage = meetingEventCoverage(channel.events);
  const known = (list: { id: string }[], id: string) => list.some(item => item.id === id);
  return <>
    <div className="personal-setting-line lark-head"><span>{t('lark.name')}<small>{t('lark.hint')}</small></span><button className="personal-switch" role="switch" aria-checked={channel.enabled} aria-label={t('lark.name')} onClick={() => save({ ...channel, enabled: !channel.enabled })}><span /></button></div>
    <div className="personal-setting-line lark-meeting-events">
      <span>{t('lark.meetingEvents')}<small id={meetingHintId}>{t(`lark.meetingEvents.${coverage}`)}</small></span>
      <button className="personal-switch" role="checkbox" aria-checked={coverage === 'all' ? true : coverage === 'none' ? false : 'mixed'} aria-label={t('lark.meetingEvents')} aria-describedby={meetingHintId} disabled={coverage === 'unknown'} onClick={() => save(withPostMeetingEvents(channel, coverage !== 'all'))}>
        <span>{(coverage === 'partial' || coverage === 'unknown') && <IconMinus size={11} />}</span>
      </button>
    </div>
    {channel.enabled && <div className="lark-body">
      <div className="lark-status" role="status">
        {health === 'no-cli' ? <p>{t('lark.noCli')}</p>
          : health === 'no-login' ? <p>{t('lark.noLogin')}</p>
          : health === 'no-agent' ? <p>{t('pet.needsSetup')}</p>
          : health === 'failed' ? <p className="personal-warning">{t('lark.failed')} <code>{status?.lastError}</code></p>
          : <p>{status?.lastRunAt ? t('lark.lastRun', timeAgo(status.lastRunAt, Date.now()), status.lastSignals) : t('lark.notYet')}</p>}
        {(health === 'ok' || health === 'failed') && <button className="text-btn" disabled={running} onClick={run}>{t(running ? 'lark.reading' : health === 'failed' ? 'common.retry' : 'lark.runNow')}</button>}
      </div>
      {runError && <p className="panel-error" role="alert">{runError}</p>}
      <RealtimeStatus status={status} />
      <div className="lark-group"><span>{t('lark.people')}</span><div className="lark-items">
        {channel.people.map(person => <div className="lark-item" key={person.id}><span>{person.name}</span>
          <Popover label={t('lark.role')} placement="below" align="end" panelClassName="personal-models" triggerClassName="lark-role" trigger={<>{t(`lark.role.${(LARK_ROLES as readonly string[]).includes(person.role ?? '') ? person.role as typeof LARK_ROLES[number] : 'other'}`)}<IconChevron size={12} open /></>}>
            {close => LARK_ROLES.map(role => <button key={role} className="pop-item compact" role="menuitemradio" aria-checked={(person.role ?? 'other') === role} onClick={() => { close(); save({ ...channel, people: channel.people.map(p => p.id === person.id ? { ...p, role } : p) }); }}>{t(`lark.role.${role}`)}</button>)}
          </Popover>
          <button className="lark-remove" aria-label={t('lark.remove', person.name)} onClick={() => save({ ...channel, people: channel.people.filter(p => p.id !== person.id) })}><IconClose size={13} /></button></div>)}
        <Popover label={t('lark.addPerson')} placement="below" panelClassName="personal-models" triggerClassName="lark-add" trigger={<><IconPlus size={13} />{t('lark.addPerson')}</>}>
          {close => <LarkSearch api={api} kind="people" onPick={hit => { close(); if (!known(channel.people, hit.id)) save({ ...channel, people: [...channel.people, { id: hit.id, name: hit.name, role: 'other' }] }); }} />}
        </Popover>
      </div></div>
      <div className="lark-group"><span>{t('lark.chats')}</span><div className="lark-items">
        {channel.chats.map(chat => <div className="lark-item" key={chat.id}><span>{chat.name}</span><button className="lark-remove" aria-label={t('lark.remove', chat.name)} onClick={() => save({ ...channel, chats: channel.chats.filter(c => c.id !== chat.id) })}><IconClose size={13} /></button></div>)}
        <Popover label={t('lark.addChat')} placement="below" panelClassName="personal-models" triggerClassName="lark-add" trigger={<><IconPlus size={13} />{t('lark.addChat')}</>}>
          {close => <LarkSearch api={api} kind="chats" onPick={hit => { close(); if (!known(channel.chats, hit.id)) save({ ...channel, chats: [...channel.chats, { id: hit.id, name: hit.name }] }); }} />}
        </Popover>
      </div></div>
      <div className="lark-group"><span>{t('lark.interval')}</span>
        <Popover label={t('lark.interval')} placement="below" panelClassName="personal-models" triggerClassName="context-btn" trigger={<>{t('lark.everyMinutes', channel.intervalMin)}<IconChevron size={13} open /></>}>
          {close => LARK_INTERVALS.map(minutes => <button key={minutes} className="pop-item compact" role="menuitemradio" aria-checked={channel.intervalMin === minutes} onClick={() => { close(); save({ ...channel, intervalMin: minutes }); }}>{t('lark.everyMinutes', minutes)}</button>)}
        </Popover>
      </div>
      <small className="personal-talk-hint">{t('personal.talkHint')}</small>
    </div>}
  </>;
}
