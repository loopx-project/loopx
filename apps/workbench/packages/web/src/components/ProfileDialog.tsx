import { useEffect, useRef, useState } from 'react';
import { ApiError, type Api } from '../api';
import { t } from '../i18n';
import { DialogSurface } from './Dialog';
import { SettingsSection, usePageDraft, usePageNavigation } from './PageNavigation';

/** What the person cares about, written by hand: a sheet of paper, not a code editor. */
export function ProfileDialog({ api, onClose, onSaved, inline = false }: { api: Api; onClose: () => void; onSaved: (isTemplate: boolean) => void; inline?: boolean }) {
  const [text, setText] = useState<string | null>(null);
  const [saved, setSaved] = useState('');
  const [rev, setRev] = useState<number | undefined>();
  const [path, setPath] = useState('');
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [busy, setBusy] = useState(false);
  // The version someone else saved while this one was being written, kept beside it until the person chooses.
  const [newer, setNewer] = useState<string | null>(null);
  const [leaving, setLeaving] = useState(false);
  const field = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    let live = true;
    setError('');
    void api.personalProfile().then(profile => {
      if (!live) return;
      setText(profile.text); setSaved(profile.text); setRev(profile.rev); setPath(profile.path);
    }).catch(e => { if (live) setError(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [api, retry]);
  const release = usePageDraft(inline && text !== null && text !== saved, inline && busy);
  const navigate = usePageNavigation();
  const loaded = text !== null;
  useEffect(() => { if (loaded) field.current?.focus(); }, [loaded]);
  const save = async (atRev = rev) => {
    if (text === null || busy) return;
    setBusy(true); setError('');
    try {
      const profile = await api.savePersonalProfile(text, atRev);
      setSaved(profile.text); release(); onSaved(profile.isTemplate);
      if (!inline) onClose();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        // Someone else saved first. What is written here stays; the person decides which one wins.
        const latest = await api.personalProfile().catch(() => null);
        if (latest) { setRev(latest.rev); setNewer(latest.text); }
        else setError(e.message);
      } else setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };
  const close = () => { if (inline) { navigate(onClose); return; } if (text !== null && text !== saved) setLeaving(true); else onClose(); };
  const content = <>
    <h2 id="profile-title" className={inline ? 'sr-only' : undefined}>{t('profile.title')}</h2>
    {text === null ? (error
      ? <p className="profile-state" role="alert">{error} <button className="ghost" onClick={() => setRetry(n => n + 1)}>{t('common.retry')}</button></p>
      : <p className="profile-state" role="status">{t('common.loading')}</p>)
      : <textarea ref={field} className="profile-field" aria-labelledby="profile-title" value={text} onChange={e => { setText(e.target.value); setLeaving(false); }} onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !e.nativeEvent.isComposing) { e.preventDefault(); void save(); } }} />}
    {path && <p className="profile-path">{t('profile.path', path)}</p>}
    {text !== null && error && <p className="panel-error" role="alert">{error}</p>}
    {newer !== null && <div className="profile-notice" role="alert"><p>{t('profile.conflict')}</p><div>
      <button className="ghost" onClick={() => { setText(newer); setSaved(newer); setNewer(null); }}>{t('profile.loadLatest')}</button>
      <button className="ghost" disabled={busy} onClick={() => { setNewer(null); void save(); }}>{t('profile.overwrite')}</button>
    </div></div>}
    {leaving && <div className="profile-notice" role="alert"><p>{t('profile.discardTitle')}</p><div>
      <button className="ghost" onClick={onClose}>{t('profile.discard')}</button>
      <button className="ghost" onClick={() => { setLeaving(false); field.current?.focus(); }}>{t('profile.keepWriting')}</button>
    </div></div>}
    <div className="dialog-actions">
      <button className="ghost" disabled={busy} onClick={close}>{t('common.cancel')}</button>
      <button className="primary" disabled={busy || text === null || newer !== null} onClick={() => void save()}>{t('personal.save')}</button>
    </div>
  </>;
  return inline ? <SettingsSection title={t('profile.title')} className="profile-page">{content}</SettingsSection>
    : <DialogSurface className="profile-dialog" labelledBy="profile-title" busy={busy} onClose={close}>{content}</DialogSurface>;

}
