import { useEffect, useState } from 'react';
import type { AutoTitlePass, AutoTitleSettings, AutoTitleStatus, ExtensionViewProps } from '@calli/extension-api/web';
import { t } from './i18n';

type Loaded = { settings: AutoTitleSettings; status: AutoTitleStatus; pass: AutoTitlePass | null; defaultRules: string };

/** The settings of 自动取名: who names, when, by which rules, and a dry run on one thread. */
export function AutoTitleView({ host }: ExtensionViewProps) {
  const locale = host.locale();
  const api = host.autotitle;
  const { Popover, PopoverOption, AgentMark, EmptyState, Icon } = host.ui;
  const [data, setData] = useState<Loaded | null>(null);
  const [failed, setFailed] = useState(false);
  const [models, setModels] = useState<{ id: string; label: string }[]>([]);
  const [rules, setRules] = useState('');
  const [saving, setSaving] = useState<'idle' | 'saving' | 'saved'>('idle');
  const [threads, setThreads] = useState(() => host.threads.snapshot());
  const [tryOn, setTryOn] = useState<string | null>(null);
  const [trial, setTrial] = useState<{ state: 'idle' | 'running' } | { state: 'done'; title: string } | { state: 'error'; message: string }>({ state: 'idle' });
  const [organizing, setOrganizing] = useState<{ kind: 'idle' } | { kind: 'queued'; n: number } | { kind: 'none' } | { kind: 'error' }>({ kind: 'idle' });

  const load = () => {
    if (!api) return;
    setFailed(false);
    api.load().then(value => { setData(value); setRules(value.settings.rules); }).catch(() => setFailed(true));
  };
  useEffect(load, [api]);
  useEffect(() => host.threads.subscribe(setThreads), [host]);
  // While a backlog pass runs, its progress is worth watching.
  useEffect(() => {
    if (!api || !data?.pass) return;
    const timer = setTimeout(() => void api.load().then(value => setData(value)).catch(() => undefined), 2000);
    return () => clearTimeout(timer);
  }, [api, data]);
  const agent = data?.settings.agent ?? null;
  useEffect(() => {
    setModels([]);
    if (api && agent) api.models(agent).then(setModels).catch(() => setModels([]));
  }, [api, agent]);

  if (!api) return <div className="autotitle-page"><EmptyState text={t(locale, 'unavailable')} /></div>;
  if (failed) return (
    <div className="autotitle-page">
      <p className="autotitle-error">{t(locale, 'loadFailed')}</p>
      <button type="button" className="autotitle-button" onClick={load}>{t(locale, 'retry')}</button>
    </div>
  );
  if (!data) return <div className="autotitle-page" aria-busy="true" />;

  const { settings, status, pass } = data;
  const save = async (patch: Partial<AutoTitleSettings>) => {
    const next = await api.save(patch);
    setData(d => (d ? { ...d, settings: next } : d));
    return next;
  };
  const saveRules = async () => {
    setSaving('saving');
    try { await save({ rules }); setSaving('saved'); } catch { setSaving('idle'); }
  };
  const agents = host.agents().filter(a => a.available);
  const picked = agents.find(a => a.id === settings.agent);
  const modelLabel = settings.model ? models.find(m => m.id === settings.model)?.label ?? settings.model : t(locale, 'defaultModel');
  const recent = threads.slice().sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)).slice(0, 12);
  const target = recent.find(th => th.id === tryOn) ?? recent[0];
  const run = async () => {
    if (!target) return;
    setTrial({ state: 'running' });
    try {
      if (rules !== settings.rules) await save({ rules });
      const { title } = await api.preview(target.id);
      setTrial({ state: 'done', title });
    } catch (error) {
      setTrial({ state: 'error', message: error instanceof Error ? error.message : String(error) });
    }
  };

  const runOrganize = async () => {
    setOrganizing({ kind: 'idle' });
    try {
      const { queued } = await api.organize();
      setOrganizing(queued > 0 ? { kind: 'queued', n: queued } : { kind: 'none' });
      if (queued > 0) { const value = await api.load(); setData(value); }
    } catch {
      setOrganizing({ kind: 'error' });
    }
  };

  return (
    <div className="autotitle-page">
      {/* The bar above already names the page. */}
      <p className="autotitle-lead">{t(locale, 'lead')}</p>

      <section className="autotitle-section">
        <h2>{t(locale, 'agent')}</h2>
        <div className="autotitle-row">
          <Popover label={t(locale, 'agent')} placement="below" triggerClassName="autotitle-button" trigger={
            picked ? <AgentMark agentId={picked.id} label /> : <span className="autotitle-muted">{t(locale, 'chooseAgent')}</span>
          }>
            {close => agents.map(a => (
              <PopoverOption key={a.id} selected={a.id === settings.agent} onSelect={() => { void save({ agent: a.id }); close(); }}>
                <AgentMark agentId={a.id} label />
              </PopoverOption>
            ))}
          </Popover>
          {picked && (
            <Popover label={t(locale, 'model')} placement="below" triggerClassName="autotitle-button" trigger={<span>{modelLabel}</span>}>
              {close => [{ id: '', label: t(locale, 'defaultModel') }, ...models.filter(m => m.id !== 'default')].map(m => (
                <PopoverOption key={m.id || 'default'} selected={(settings.model ?? '') === m.id} onSelect={() => { void save({ model: m.id || null }); close(); }}>
                  {m.label}
                </PopoverOption>
              ))}
            </Popover>
          )}
        </div>
        {!picked && <p className="autotitle-note">{t(locale, 'noAgent')}</p>}
        {picked?.permissionModes && !picked.permissionModes.includes('ask') && (
          <p className="autotitle-note">{t(locale, 'noAsk')}</p>
        )}
      </section>

      <section className="autotitle-section">
        <h2>{t(locale, 'when')}</h2>
        <div className="autotitle-choices" role="radiogroup" aria-label={t(locale, 'when')}>
          {(['finished', 'sent'] as const).map(value => (
            <button key={value} type="button" role="radio" aria-checked={settings.trigger === value}
              className={`autotitle-choice${settings.trigger === value ? ' on' : ''}`} onClick={() => void save({ trigger: value })}>
              <span className="autotitle-choice-label">{t(locale, value)}</span>
              <span className="autotitle-choice-hint">{t(locale, value === 'finished' ? 'finishedHint' : 'sentHint')}</span>
            </button>
          ))}
        </div>
        <button type="button" role="switch" aria-checked={settings.again} className={`autotitle-switch${settings.again ? ' on' : ''}`} onClick={() => void save({ again: !settings.again })}>
          <span className="autotitle-switch-track" aria-hidden="true"><span /></span>
          <span>
            <span className="autotitle-choice-label">{t(locale, 'again')}</span>
            <span className="autotitle-choice-hint">{t(locale, 'againHint')}</span>
          </span>
        </button>
      </section>

      <section className="autotitle-section">
        <h2>{t(locale, 'rules')}</h2>
        <p className="autotitle-note">{t(locale, 'rulesHint')}</p>
        <textarea className="autotitle-rules" value={rules} rows={5} spellCheck={false} aria-label={t(locale, 'rules')}
          onChange={e => { setRules(e.target.value); setSaving('idle'); }} onBlur={() => { if (rules !== settings.rules) void saveRules(); }} />
        <div className="autotitle-row">
          <button type="button" className="autotitle-button" disabled={rules === settings.rules || saving === 'saving'} onClick={() => void saveRules()}>
            {saving === 'saving' ? t(locale, 'saving') : saving === 'saved' && rules === settings.rules ? t(locale, 'saved') : t(locale, 'save')}
          </button>
          {rules !== data.defaultRules && (
            <button type="button" className="autotitle-link" onClick={() => setRules(data.defaultRules)}>{t(locale, 'resetRules')}</button>
          )}
        </div>
      </section>

      <section className="autotitle-section">
        <h2>{t(locale, 'tryTitle')}</h2>
        {recent.length === 0 ? <p className="autotitle-note">{t(locale, 'noThreads')}</p> : (
          <>
            <div className="autotitle-row">
              <Popover label={t(locale, 'tryOn')} placement="below" triggerClassName="autotitle-button autotitle-thread" trigger={<span>{target?.title}</span>}>
                {close => recent.map(th => (
                  <PopoverOption key={th.id} selected={th.id === target?.id} onSelect={() => { setTryOn(th.id); setTrial({ state: 'idle' }); close(); }}>
                    {th.title}
                  </PopoverOption>
                ))}
              </Popover>
              <button type="button" className="autotitle-button primary" disabled={!picked || trial.state === 'running'} onClick={() => void run()}>
                {trial.state === 'running' ? t(locale, 'trying') : t(locale, 'tryTitle')}
              </button>
            </div>
            {trial.state === 'done' && (
              <div className="autotitle-result" role="status">
                <span className="autotitle-muted">{t(locale, 'tryResult')}</span>
                <strong>{trial.title}</strong>
                <span className="autotitle-note">{t(locale, 'tryHint')}</span>
              </div>
            )}
            {trial.state === 'error' && <p className="autotitle-error" role="alert">{trial.message}</p>}
          </>
        )}
      </section>

      <section className="autotitle-section">
        <h2>{t(locale, 'organize')}</h2>
        <p className="autotitle-note">{t(locale, 'organizeHint')}</p>
        {pass ? (
          <p className="autotitle-note" role="status">{t(locale, 'organizing', pass.done, pass.total)}</p>
        ) : (
          <div className="autotitle-row">
            <button type="button" className="autotitle-button primary" disabled={!picked} onClick={() => void runOrganize()}>
              {t(locale, 'organizeRun')}
            </button>
            {organizing.kind === 'queued' && <span className="autotitle-note">{t(locale, 'organized', organizing.n)}</span>}
            {organizing.kind === 'none' && <span className="autotitle-note">{t(locale, 'noneToOrganize')}</span>}
            {organizing.kind === 'error' && <span className="autotitle-error" role="alert">{t(locale, 'organizeFailed')}</span>}
          </div>
        )}
      </section>

      {(status.last || status.error) && (
        <footer className="autotitle-status">
          {status.error && (!status.last || status.error.at > status.last.at)
            ? <span className="autotitle-error"><Icon name="close" size={13} /> {t(locale, 'error', status.error.message)}</span>
            : status.last && <span><Icon name="check" size={13} /> {t(locale, 'last', status.last.title)}</span>}
        </footer>
      )}
    </div>
  );
}
