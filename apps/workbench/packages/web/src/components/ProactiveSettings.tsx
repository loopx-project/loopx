import { useEffect, useState } from 'react';
import type { Api, ProactiveSettings as Settings } from '../api';
import { t } from '../i18n';
import { removeRepo } from '../proactive';
import { IconClose, IconPlus } from './icons';

const MODES = ['off', 'observe', 'auto'] as const;

/** Whether she may act on her own, in the private settings. Off shows one line; observing and acting show the repositories too. Changes wait for the page's Save. */
export function ProactiveSettings({ api, draft, canPick, onDraft, onPickRepo, onLoaded }: { api: Api; draft: Settings | null; canPick: boolean; onDraft: (next: Settings) => void; onLoaded?: (value: Settings) => void; onPickRepo: (current: Settings) => void }) {
  const [value, setValue] = useState<Settings | null>(draft);
  useEffect(() => { if (draft) setValue(draft); }, [draft]);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (draft) return;
    let live = true;
    setError('');
    void api.proactiveSettings().then(next => { if (live) { setValue(current => current ?? next.settings); onLoaded?.(next.settings); } }).catch(e => { if (live) setError(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [api, retry, onLoaded]);
  const save = (next: Settings) => {
    setValue(next);
    onDraft(next);
  };
  if (!value) return <div className="personal-setting-line"><span>{t('proactive.name')}{error ? <small className="personal-warning" role="alert">{error}</small> : <small role="status">{t('common.loading')}</small>}</span>{error && <button className="ghost" onClick={() => setRetry(n => n + 1)}>{t('common.retry')}</button>}</div>;
  return <div className="personal-setting-line proactive-settings">
    <div className="brief-head"><span>{t('proactive.name')}<small>{t('proactive.hint')}</small></span><div className="desk-segment proactive-mode" role="group" aria-label={t('proactive.name')}>{MODES.map(mode => <button key={mode} aria-pressed={value.mode === mode} onClick={() => save({ ...value, mode, enabled: mode !== 'off' })}>{t(`proactive.mode.${mode}`)}</button>)}</div></div>
    {value.mode !== 'off' && <div className="proactive-body">
      <div className="lark-group"><span>{t('proactive.repos')}</span><div className="lark-items">
        {value.repos.map(repo => <div className="lark-item proactive-repo" key={repo}><span title={repo}>{repo}</span><button className="lark-remove" aria-label={t('lark.remove', repo)} onClick={() => save(removeRepo(value, repo))}><IconClose size={13} /></button></div>)}
        <button className="lark-add" disabled={!canPick} onClick={() => onPickRepo(value)}><IconPlus size={13} />{t('proactive.addRepo')}</button>
      </div></div>
      <small>{t(value.mode === 'observe' ? 'proactive.observeHint' : value.repos.length ? 'proactive.limits' : 'proactive.noRepos')}</small>
      <small className="personal-talk-hint">{t('personal.talkHint')}</small>
    </div>}
  </div>;
}
