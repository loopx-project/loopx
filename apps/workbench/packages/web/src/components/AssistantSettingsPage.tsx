import { useCallback, useEffect, useState } from 'react';
import type { AgentInfo, Api, BriefSettings as BriefValue, LarkChannelSettings, PersonalSettings, ProactiveSettings as ProactiveValue } from '../api';
import { t } from '../i18n';
import { addRepo, settingsPayload } from '../proactive';
import { BriefSettings } from './BriefSettings';
import { DirectoryPicker } from './DirectoryPicker';
import { LarkSettings } from './LarkSettings';
import { PersonalSettingsDialog } from './PersonalSettingsDialog';
import { ProfileDialog } from './ProfileDialog';
import { ProactiveSettings } from './ProactiveSettings';
import { SettingsPage } from './SettingsPage';
import { IconSparkle } from './icons';
import { SettingsSection, usePageDraft, usePageNavigation } from './PageNavigation';

export function assistantTitle(path: string) {
  switch (path.split('/')[1]) {
    case 'profile': return t('profile.title');
    case 'lark': return t('lark.name');
    case 'brief': return t('brief.name');
    case 'proactive': return t('proactive.name');
    default: return t('personal.settings');
  }
}

/** Each feature mounts on entry; its status and full configuration are not loaded for the index. */
export function AssistantSettingsPage({ api, agents, path, onNavigate, onSave, setupNeeded, onExplore }: {
  setupNeeded?: boolean | null; onExplore?: () => void;
  api: Api; agents: AgentInfo[]; path: string; onNavigate: (path: string) => void; onSave: (value: PersonalSettings) => void;
}) {
  const [value, setValue] = useState<PersonalSettings | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const feature = path.split('/')[1];
  useEffect(() => {
    if (feature) return;
    let live = true; setError('');
    void api.personalSettings().then(next => { if (live) setValue(next); }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, [api, feature, retry]);
  if (feature === 'profile') return <ProfileDialog inline api={api} onClose={() => onNavigate('assistant')} onSaved={() => {}} />;
  if (feature === 'lark' || feature === 'brief' || feature === 'proactive') return <AssistantFeature key={feature} api={api} feature={feature} />;
  if (value) return <PersonalSettingsDialog api={api} agents={agents} initial={value} setupNeeded={setupNeeded} onExplore={onExplore} onSave={next => { setValue(next); onSave(next); }} onClose={() => onNavigate('')} onNavigate={onNavigate} />;
  return <SettingsSection title={t('personal.settings')}>
    {error ? <p role="alert" className="panel-error">{error} <button className="ghost" onClick={() => setRetry(n => n + 1)}>{t('common.retry')}</button></p> : <p role="status">{t('common.loading')}</p>}
  </SettingsSection>;
}

function AssistantFeature({ api, feature }: { api: Api; feature: 'lark' | 'brief' | 'proactive' }) {
  const [lark, setLark] = useState<LarkChannelSettings | null>(null);
  const [brief, setBrief] = useState<BriefValue | null>(null);
  const [proactive, setProactive] = useState<ProactiveValue | null>(null);
  const [saved, setSaved] = useState('null');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [savedNotice, setSavedNotice] = useState(false);
  const [roots, setRoots] = useState<string[] | null>(null);
  const [picking, setPicking] = useState(false);
  const current = feature === 'lark' ? lark : feature === 'brief' ? brief : proactive;
  const loaded = useCallback((value: LarkChannelSettings | BriefValue | ProactiveValue) => setSaved(JSON.stringify(value)), []);
  const dirty = current !== null && JSON.stringify(current) !== saved;
  usePageDraft(dirty, busy);
  const save = async () => {
    setBusy(true); setError(''); setSavedNotice(false);
    try {
      let next = current;
      if (lark) { const value = await api.saveLarkChannel(lark); setLark(value); next = value; }
      if (brief) { const value = await api.saveBriefSettings(brief); setBrief(value); next = value; }
      if (proactive) { const value = await api.saveProactiveSettings(settingsPayload(proactive)); setProactive(value); next = value; }
      setSaved(JSON.stringify(next)); setSavedNotice(true);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  const pickRepo = async (value: ProactiveValue) => {
    setProactive(value); setError('');
    try { setRoots((await api.workdirs()).workdirs); setPicking(true); }
    catch (e) { setError(String(e)); }
  };
  return <SettingsSection busy={busy} title={assistantTitle(`assistant/${feature}`)} className="personal-settings" actions={<>
    <span role="status">{savedNotice && JSON.stringify(current) === saved ? t('settings.saved') : ''}</span>
    <button className="primary" disabled={!dirty || busy} onClick={() => void save()}>{t(busy ? 'general.saving' : 'personal.save')}</button>
  </>}>
    {feature === 'lark' && <LarkSettings api={api} draft={lark} onDraft={setLark} onLoaded={loaded} />}
    {feature === 'brief' && <BriefSettings api={api} draft={brief} onDraft={setBrief} onLoaded={loaded} />}
    {feature === 'proactive' && <ProactiveSettings api={api} draft={proactive} canPick={!busy} onDraft={setProactive} onLoaded={loaded} onPickRepo={value => void pickRepo(value)} />}
    {error && <p className="panel-error" role="alert">{error}</p>}
    {picking && roots && <DirectoryPicker api={api} allowedOnly roots={roots} selected="" machine="" onRootsChange={() => {}} onManageRoots={() => {}}
      onSelect={path => { if (proactive) setProactive(addRepo(proactive, path)); }} onClose={() => setPicking(false)} />}
  </SettingsSection>;
}

/** The desk keeps its pending delegation while the same settings pages take its place. */
export function AssistantSettings({ api, agents, onSave, onClose, setupNeeded, onExplore }: {
  setupNeeded?: boolean | null; onExplore?: () => void;
  api: Api; agents: AgentInfo[]; onSave: (value: PersonalSettings) => void; onClose: () => void;
}) {
  const [path, setPath] = useState('assistant');
  const navigate = usePageNavigation();
  return <div className="settings-standalone"><SettingsPage path={path} detailTitle={assistantTitle(path)} onClose={() => navigate(onClose)}
    onNavigate={next => navigate(() => setPath(next))} groups={[{ label: t('settings.preferences'), entries: [{
      id: 'assistant', label: t('personal.settings'), icon: IconSparkle,
      render: () => <AssistantSettingsPage api={api} agents={agents} path={path} setupNeeded={setupNeeded} onExplore={onExplore ? () => navigate(onExplore) : undefined} onNavigate={next => navigate(() => setPath(next))} onSave={onSave} />,
    }] }]} /></div>;
}
