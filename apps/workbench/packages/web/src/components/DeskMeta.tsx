import { useEffect, useState, type ReactNode } from 'react';
import type { Api } from '../api';
import { briefStatus, type BriefStatus } from '../desk';
import { formatTime, t } from '../i18n';
import { larkHealth, larkSourceState, meetingEventCoverage } from '../lark';
import { useIsNarrow } from '../hooks';
import { timeAgo } from '../sessions';
import { IconMore, IconRefresh } from './icons';
import { useLark } from './LarkSettings';
import { Popover } from './Popover';

/** Today's brief as it actually went out, or nothing if none did. Only asked for while briefs are on. */
function useBriefStatus(api: Api, refreshKey: number, now: Date): BriefStatus | null {
  const [status, setStatus] = useState<BriefStatus | null>(null);
  const day = now.toDateString();
  useEffect(() => {
    let live = true;
    void (async () => {
      const settings = await api.briefSettings();
      if (!settings.enabled) { if (live) setStatus(null); return; }
      const [morning, evening, push] = await Promise.all([api.lastPersonalBrief('morning'), api.lastPersonalBrief('evening'), api.pushStatus(true)]);
      if (live) setStatus(briefStatus({ morning, evening }, push.records, new Date()));
    })().catch(() => undefined);
    return () => { live = false; };
  }, [api, refreshKey, day]);
  return status;
}

/** The one line under the date: the count first, then how the signals and the brief stand. */
export function DeskMeta({ api, count, stale, now, refreshKey, onConfigure, onChat }: { api: Api; count: string; stale: number; now: Date; refreshKey: number; onConfigure: () => void; onChat?: () => void }) {
  const narrow = useIsNarrow();
  const { channel, status, loading, loadError, load, run, running, runError } = useLark(api);
  const brief = useBriefStatus(api, refreshKey, now);
  const health = larkHealth(channel, status);
  const sourceState = larkSourceState(channel, status, loading, loadError);
  const coverage = meetingEventCoverage(channel?.events);

  // What needs the person stays on the line; on a phone everything else folds into the menu.
  const urgent: ReactNode[] = [];
  const quiet: ReactNode[] = [];
  if (stale > 0) quiet.push(<span key="stale" className="personal-warning">{t('desk.stale', stale)}</span>);
  if (sourceState === 'load-failed') {
    urgent.push(<span key="lark-load" className="desk-lark problem" role="alert" title={loadError}>
      <span>{t('lark.deskLoadFailed')}</span><button onClick={load}>{t('common.retry')}</button>
    </span>);
  } else if (sourceState !== 'ready') {
    quiet.push(<span key="lark-source" className={`desk-source-state ${sourceState}`} role="status">
      <span>{t(sourceState === 'loading' ? 'lark.deskLoading' : sourceState === 'off' ? 'lark.deskOff' : 'lark.deskUnknown')}</span>
      {sourceState !== 'loading' && <button onClick={onConfigure}>{t('lark.open')}</button>}
    </span>);
  } else {
    if (health !== 'ok' || runError) {
      const fixable = health === 'no-cli' || health === 'no-login' || health === 'no-agent';
      urgent.push(<span key="lark" className="desk-lark problem" role="alert" title={runError || status?.lastError || undefined}>
        <span>{t(health === 'no-cli' ? 'lark.deskNoCli' : health === 'no-login' ? 'lark.deskNoLogin' : health === 'no-agent' ? 'lark.deskNoAgent' : 'lark.deskFailed')}</span>
        <button onClick={fixable ? onConfigure : run} disabled={running}>{t(fixable ? 'lark.open' : 'common.retry')}</button>
      </span>);
    } else {
      const ago = status?.lastRunAt ? timeAgo(status.lastRunAt, now.getTime()) : '';
      quiet.push(<span key="lark" className="desk-lark">{t('lark.name')}<span aria-hidden="true">·</span>{ago ? t('lark.readAgo', ago) : t('lark.notYet')}
        <button className="desk-lark-refresh" aria-label={t('lark.refresh')} title={t('lark.refresh')} disabled={running} onClick={run}><IconRefresh size={12} /></button></span>);
    }
    if (coverage !== 'all') {
      const item = <span key="lark-meeting" className={`desk-lark desk-lark-meeting${coverage !== 'unknown' ? ' problem' : ''}`} role="status">
        <span>{t(`lark.deskMeeting.${coverage}`)}</span><button onClick={onConfigure}>{t('lark.open')}</button>
      </span>;
      (coverage === 'unknown' ? quiet : urgent).push(item);
    }
  }
  if (brief) {
    const time = formatTime(brief.at, { hour: '2-digit', minute: '2-digit' });
    const item = <span key="brief" className={`desk-brief-state${brief.state === 'failed' ? ' problem' : ''}`}>
      <Popover label={t(`brief.kind.${brief.kind}`)} placement="below" panelClassName="desk-menu" triggerClassName="desk-brief-open" trigger={t(`brief.kind.${brief.kind}`)}>
        {() => <p className="desk-brief-text">{brief.state === 'sent' ? brief.text : brief.reason || t('brief.failedHint')}</p>}
      </Popover>
      {t(brief.state === 'sent' ? 'brief.sent' : 'brief.failed', time)}
    </span>;
    (brief.state === 'failed' ? urgent : quiet).push(item);
  }

  // A phone has room for one problem beside the count; a second one tints the menu instead of being cut off.
  const shown = narrow ? urgent.slice(0, 1) : [...quiet, ...urgent];
  const folded = narrow ? [...urgent.slice(1), ...quiet] : [];
  return <div className="desk-summary">
    <span className="desk-count-now">{count}</span>
    {shown.map((item, i) => <span className="desk-meta-item" key={i}><span aria-hidden="true">·</span>{item}</span>)}
    {folded.length > 0 && <Popover label={t('desk.metaMore')} placement="below" panelClassName="desk-menu" triggerClassName={`desk-meta-more${urgent.length > 1 ? ' problem' : ''}`} triggerLabel={t('desk.metaMore')} trigger={<IconMore size={15} />}>
      {() => <div className="desk-meta-list">{folded}</div>}
    </Popover>}
    {onChat && <span className="desk-meta-item"><span aria-hidden="true">·</span><button className="desk-brief-open" onClick={onChat}>{t('desk.chat')}</button></span>}
    {/* Always reachable: until she is configured this is the only way to reach her settings. */}
    <span className="desk-meta-item"><span aria-hidden="true">·</span><button className="desk-brief-open" onClick={onConfigure}>{t('desk.configure')}</button></span>
  </div>;
}
