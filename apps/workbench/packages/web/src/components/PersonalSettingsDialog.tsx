import { useEffect, useState } from 'react';
import type { AgentInfo, Api, ModelOption, PersonalSettings } from '../api';
import { t } from '../i18n';
import { SettingsSection, usePageDraft } from './PageNavigation';
import { DirectoryPicker } from './DirectoryPicker';
import { AgentPicker } from './Pickers';
import { pickAgent, startsNewConversation } from '../personal-settings';
import { Popover } from './Popover';
import { IconChevron, IconFolder, IconSearch } from './icons';
import { readPetPresence, savePetPresence } from './Pet';
import type { PetPresence } from '../pet';

export function PersonalSettingsDialog({ api, agents, initial, onSave, onClose, onNavigate, setupNeeded, onExplore }: {
  setupNeeded?: boolean | null; onExplore?: () => void;
  api: Api; agents: AgentInfo[]; initial: PersonalSettings; onSave: (value: PersonalSettings) => void; onClose: () => void; onNavigate?: (path: string) => void;
}) {
  const [value, setValue] = useState(initial);
  const [roots, setRoots] = useState<string[] | null>(null);
  const [models, setModels] = useState<ModelOption[] | null>(null);
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState('');
  const [modelError, setModelError] = useState('');
  const [saving, setSaving] = useState(false);
  const [retry, setRetry] = useState(0);
  const [query, setQuery] = useState('');
  const [saved, setSaved] = useState(JSON.stringify(initial));
  const [savedNotice, setSavedNotice] = useState(false);
  const [petPresence, setPetPresence] = useState<PetPresence>(readPetPresence);
  usePageDraft(JSON.stringify(value) !== saved, saving);
  useEffect(() => { let live = true; void api.workdirs().then(v => { if (live) setRoots(v.workdirs); }).catch(e => { if (live) setError(String(e)); }); return () => { live = false; }; }, [api, initial.defaultDirectory, retry]);
  useEffect(() => {
    let live = true; setModels(null); setQuery(''); setModelError('');
    if (value.agent) void api.models(value.agent).then(v => { if (live) setModels(v); }).catch(e => { if (live) setModelError(String(e)); });
    return () => { live = false; };
  }, [api, value.agent, retry]);
  const isDefault = value.directory === initial.defaultDirectory || (value.directory === initial.directory && initial.directoryIsDefault);
  const directoryLabel = isDefault ? t('personal.defaultDirectory', initial.defaultDirectoryDisplay ?? initial.directoryDisplay ?? value.directory ?? '') : value.directory === initial.directory ? initial.directoryDisplay ?? value.directory : value.directory;
  if (picking && roots) return <DirectoryPicker api={api} allowedOnly defaultDirectory={initial.defaultDirectory} roots={roots} selected={value.directory ?? ''} machine="" onRootsChange={() => {}} onManageRoots={() => {}} onSelect={directory => setValue({ ...value, directory })} onClose={() => setPicking(false)} />;
  const save = async () => {
    setSaving(true); setError(''); setSavedNotice(false);
    try {
      const next = await api.savePersonalSettings({ ...value, directory: isDefault ? null : value.directory });
      setValue(next); setSaved(JSON.stringify(next)); setSavedNotice(true); onSave(next);
    } catch (e) { setError(String(e)); } finally { setSaving(false); }
  };
  return <SettingsSection busy={saving} title={t('personal.settings')} className="personal-settings" actions={<>
    <span role="status">{savedNotice && JSON.stringify(value) === saved ? t('settings.saved') : ''}</span>
    <button className="primary" disabled={saving || !value.directory || !value.agent} onClick={() => void save()}>{t(saving ? 'general.saving' : 'personal.save')}</button>
  </>}>
    {onExplore && <div className="personal-setting-line"><span>{t(setupNeeded ? 'explore.entryHint' : 'explore.again')}</span><button type="button" className={setupNeeded ? 'primary' : 'ghost'} onClick={onExplore}>{t(setupNeeded ? 'explore.entry' : 'explore.again')}</button></div>}
    <div className="personal-setting-line"><span>{t('personal.directory')}</span><button className="context-btn personal-directory" disabled={!roots} onClick={() => setPicking(true)}><IconFolder size={16} />{directoryLabel || t('personal.chooseDirectory')}</button></div>
    <div className="personal-setting-line"><span>{t('personal.agent')}</span><AgentPicker api={api} agents={agents} value={value.agent ?? ''} onChange={agent => setValue(pickAgent(value, agent))} /></div>
    <div className="personal-setting-line"><span>{t('personal.model')}</span><Popover label={t('personal.model')} placement="below" panelClassName="personal-models" triggerClassName="context-btn" disabled={!value.agent} trigger={<>{!models && !modelError ? t('common.loading') : modelError ? t('common.retry') : models?.find(m => m.id === value.model)?.label ?? value.model ?? t('auto.modelDefault')}<IconChevron size={12} open /></>}>
      {close => <>{models && models.length > 10 && <label className="pop-search"><IconSearch size={15} /><input aria-label={t('personal.searchModels')} placeholder={t('personal.searchModels')} value={query} onChange={e => setQuery(e.target.value)} /></label>}<div className="pop-scroll"><button className="pop-item compact" role="menuitemradio" aria-checked={!value.model} onClick={() => { setValue({ ...value, model: null }); close(); }}>{t('auto.modelDefault')}</button>
        {models?.filter(m => `${m.label} ${m.id}`.toLowerCase().includes(query.trim().toLowerCase())).map(m => <button key={m.id} className="pop-item compact" role="menuitemradio" aria-checked={m.id === value.model} onClick={() => { setValue({ ...value, model: m.id }); close(); }}>{m.label}</button>)}
        </div>{!models && !modelError && <p role="status">{t('common.loading')}</p>}
        {modelError && <p className="panel-error" role="alert">{modelError} <button className="ghost" onClick={() => setRetry(n => n + 1)}>{t('common.retry')}</button></p>}
      </>}
    </Popover></div>
    <div className="personal-setting-line"><span>{t('pet.presenceLabel')}<small>{t('pet.presenceHint')}</small></span>
      <div className="desk-segment" role="group" aria-label={t('pet.presenceLabel')}>
        {(['always', 'event', 'off'] as const).map(mode => <button key={mode} type="button" aria-pressed={petPresence === mode}
          onClick={() => { setPetPresence(mode); savePetPresence(mode); }}>{t(mode === 'always' ? 'pet.presence.always' : mode === 'event' ? 'pet.presence.event' : 'pet.presence.off')}</button>)}
      </div></div>
    {onNavigate && <div className="settings-related">{([
      ['profile', 'profile.title'], ['lark', 'lark.name'], ['brief', 'brief.name'], ['proactive', 'proactive.name'],
    ] as const).map(([id, label]) => <button className="settings-link-row" key={id} onClick={() => onNavigate(`assistant/${id}`)}><span>{t(label)}</span><IconChevron size={15} /></button>)}</div>}
    {startsNewConversation(initial, value) && <p className="personal-warning personal-fork-hint" role="status">{t('personal.newConversation')}</p>}
    {error && <p className="panel-error" role="alert">{error} {!roots && <button className="ghost" onClick={() => { setError(''); setRetry(n => n + 1); }}>{t('common.retry')}</button>}</p>}
  </SettingsSection>;
}
