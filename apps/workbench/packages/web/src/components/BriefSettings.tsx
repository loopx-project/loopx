import { useEffect, useState } from 'react';
import type { Api, BriefSettings as Settings } from '../api';
import { briefTimes } from '../desk';
import { t } from '../i18n';
import { IconChevron } from './icons';
import { Popover } from './Popover';

/** The morning and evening brief in the private settings. Changes wait for the page's Save. */
export function BriefSettings({ api, draft, onDraft, onLoaded }: { api: Api; draft: Settings | null; onDraft: (next: Settings) => void; onLoaded?: (value: Settings) => void }) {
  const [value, setValue] = useState<Settings | null>(draft);
  useEffect(() => { if (draft) setValue(draft); }, [draft]);
  const [pushOn, setPushOn] = useState(true);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let live = true;
    setError('');
    void api.briefSettings().then(next => { if (live) { setValue(current => draft ?? current ?? next); onLoaded?.(next); } }).catch(e => { if (live) setError(e instanceof Error ? e.message : String(e)); });
    void api.pushSettings().then(push => { if (live) setPushOn(push.enabled); }).catch(() => undefined);
    return () => { live = false; };
  }, [api, retry, onLoaded]);
  const save = (next: Settings) => {
    setValue(next);
    onDraft(next);
  };
  if (!value) return <div className="personal-setting-line"><span>{t('brief.name')}{error ? <small className="personal-warning" role="alert">{error}</small> : <small role="status">{t('common.loading')}</small>}</span>{error && <button className="ghost" onClick={() => setRetry(n => n + 1)}>{t('common.retry')}</button>}</div>;
  return <div className="personal-setting-line brief-settings">
    <div className="brief-head"><span>{t('brief.name')}<small>{t('brief.hint')}</small></span><button className="personal-switch" role="switch" aria-checked={value.enabled} aria-label={t('brief.name')} onClick={() => save({ ...value, enabled: !value.enabled })}><span /></button></div>
    {value.enabled && <div className="brief-times">
      {(['morning', 'evening'] as const).map(kind => <Popover key={kind} label={t(`brief.${kind}`)} placement="below" panelClassName="personal-models" triggerClassName="context-btn" trigger={<>{t(`brief.${kind}`)} {value[kind]}<IconChevron size={13} open /></>}>
        {close => <div className="pop-scroll">{briefTimes(kind).map(time => <button key={time} className="pop-item compact" role="menuitemradio" aria-checked={value[kind] === time} onClick={() => { close(); save({ ...value, [kind]: time }); }}>{time}</button>)}</div>}
      </Popover>)}
      {!pushOn && <small className="personal-warning">{t('brief.pushOff')}</small>}
    </div>}
  </div>;
}
