import { useEffect, useState } from 'react';
import type { Api, PushSettings, PushStatus } from '../api';
import { t } from '../i18n';
import { useIsCompact } from '../hooks';
import { SettingsSection, usePageDraft } from './PageNavigation';
import { IconChevron } from './icons';

function reason(value: string | null) {
  switch (value) {
    case 'cli_missing': return t('push.cliMissing');
    case 'bot_not_ready': return t('push.botMissing');
    case 'timeout': return t('push.timeout');
    case 'notice_rate_limit':
    case 'hourly_rate_limit': return t('push.dropped');
    case 'recipient_missing': return t('push.recipientMissing');
    case 'invalid_status': return t('push.invalidStatus');
    default: return t('push.failed');
  }
}
export function PushSettingsDialog({ api, onClose }: { api: Api; onClose: () => void }) {
  const compact = useIsCompact();
  const [value, setValue] = useState<PushSettings | null>(null);
  const [status, setStatus] = useState<PushStatus | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [records, setRecords] = useState<PushStatus['records'] | null>(null);
  const [recordError, setRecordError] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [tested, setTested] = useState(false);
  const [saved, setSaved] = useState('');
  const [savedNotice, setSavedNotice] = useState(false);
  usePageDraft(value !== null && JSON.stringify(value) !== saved, busy);
  const load = () => { setError(''); void Promise.all([api.pushSettings(), api.pushStatus()]).then(([v, s]) => { setValue(v); setSaved(JSON.stringify(v)); setStatus(s); }).catch(e => setError(String(e))); };
  useEffect(load, [api]);
  const loadRecords = () => { setRecords(null); setRecordError(''); void api.pushStatus(true).then(s => setRecords(s.records)).catch(e => setRecordError(String(e))); };
  const last = status?.records[0];
  const save = async () => {
    if (!value || busy) return;
    setBusy(true); setError(''); setSavedNotice(false);
    try { const next = await api.savePushSettings(value); setValue(next); setSaved(JSON.stringify(next)); setSavedNotice(true); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  return <SettingsSection busy={busy} title={t('push.title')} hint={t(compact ? 'push.hintShort' : 'push.hint')} className="push-settings"
    actions={<><span role="status">{savedNotice && value && JSON.stringify(value) === saved ? t('settings.saved') : ''}</span><button className="primary" disabled={!value || busy} onClick={() => void save()}>{t(busy ? 'general.saving' : 'personal.save')}</button></>}>
    {!value && !error && <p>{t('common.loading')}</p>}
    {value && <>
      {(['enabled', 'waiting', 'failure', 'notice', 'scheduled'] as const).map((key, i) => <div key={key}>
        <div className="personal-setting-line"><span>{t(`push.${key}`)}{key === 'enabled' && <small className={status?.botReady ? '' : 'personal-warning'}>{status?.botReady ? t('push.ready') : status ? reason(status.reason) : t('common.loading')}</small>}</span>
          <button className="personal-switch" role="switch" aria-label={t(`push.${key}`)} aria-checked={value[key]} disabled={busy} onClick={() => setValue({ ...value, [key]: !value[key] })}><span /></button>
        </div>
        {i === 0 && <>
          <label className="personal-setting-line"><span>{t('push.recipient')}</span><input value={value.recipient} onChange={e => setValue({ ...value, recipient: e.target.value })} /></label>
          <label className="personal-setting-line"><span>{t('push.address')}<small>{t('push.addressHint')}</small></span><input placeholder={t('push.optional')} value={value.baseUrl} onChange={e => setValue({ ...value, baseUrl: e.target.value })} /></label>
        </>}
      </div>)}
      <div className="push-bottom"><button className="text-btn" aria-expanded={expanded} onClick={() => { if (!expanded) loadRecords(); setExpanded(!expanded); }}>{t('push.recent')} {last ? `${new Date(last.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} ${t(`push.${last.result}`)}` : t('push.none')}<IconChevron size={12} open={expanded} /></button>
        <button className="ghost" disabled={busy} onClick={() => { setBusy(true); setError(''); setTested(false); void api.savePushSettings(value).then(() => { setSaved(JSON.stringify(value)); return api.testPush(); }).then(() => { setTested(true); return api.pushStatus(); }).then(s => { setStatus(s); if (expanded) loadRecords(); }).catch(e => setError(String(e))).finally(() => setBusy(false)); }}>{t('push.test')}</button></div>
      {tested && <p className="push-test-result" role="status">{t('push.testQueued')}</p>}
      {expanded && <div className="push-records">{!records && !recordError && <p>{t('common.loading')}</p>}{recordError && <p role="alert">{recordError} <button className="ghost" onClick={loadRecords}>{t('common.retry')}</button></p>}{records?.length === 0 && <p>{t('push.none')}</p>}{records?.map(record => <div key={record.key}><time>{new Date(record.at).toLocaleString()}</time><span>{record.title || t('push.test')}</span><span className={record.result === 'failed' ? 'personal-warning' : ''}>{t(`push.${record.result}`)}{record.reason && ` · ${reason(record.reason)}`}</span></div>)}</div>}
    </>}
    {error && <p className="panel-error" role="alert">{error} {!value && <button className="ghost" onClick={load}>{t('common.retry')}</button>}</p>}
  </SettingsSection>;
}
