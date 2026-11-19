import { useEffect, useState } from 'react';
import type { AgentInfo, Api, SupportedAgent } from '../api';
import { t } from '../i18n';
import { DialogSurface } from './Dialog';
import { Popover } from './Popover';
import { StreamBridgeOption } from './StreamBridgeOption';
import { AgentMark, IconChevron, IconPlus } from './icons';

export function AgentAccountDialog({ api, agents, onCreated, onDiscover, onClose }: {
  api: Api; agents: AgentInfo[]; onCreated: () => Promise<void>; onDiscover: () => void; onClose: () => void;
}) {
  const [supported, setSupported] = useState<SupportedAgent[] | null>(null);
  const [selected, setSelected] = useState<SupportedAgent | null>(null);
  const [sourceId, setSourceId] = useState('');
  const [label, setLabel] = useState('');
  const [streamBridge, setStreamBridge] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const load = () => {
    setError('');
    return api.supportedAgents().then(setSupported).catch(e => setError(String(e.message)));
  };
  useEffect(() => { let active = true; api.supportedAgents().then(items => { if (active) setSupported(items); }).catch(e => { if (active) setError(e.message); }); return () => { active = false; }; }, [api]);
  const sources = agents.filter(agent => agent.adapter === selected?.adapter);
  const choose = (item: SupportedAgent) => {
    setSelected(item);
    const source = agents.find(agent => agent.adapter === item.adapter);
    setSourceId(source?.id ?? '');
    setLabel(item.capabilities?.createAccount ? '' : source?.label ?? '');
    setStreamBridge(source?.streamBridgeEnabled ?? false);
  };
  const create = async () => {
    if (!selected || busy) return;
    setBusy(true); setError('');
    try {
      if (selected.capabilities?.createAccount) {
        await api.createAgentAccount({ adapter: selected.adapter, sourceId: sourceId || undefined, label: label.trim() || undefined });
      } else if (sourceId) {
        await api.updateAgent(sourceId, { streamBridge, ...(label.trim() ? { label: label.trim() } : {}) });
      } else {
        let id = selected.adapter;
        for (let number = 2; agents.some(agent => agent.id === id); number++) id = `${selected.adapter}-${number}`;
        await api.addAgent({ id, adapter: selected.adapter, label: label.trim() || selected.label, streamBridge });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e)); setBusy(false); return;
    }
    await onCreated();
  };
  return <DialogSurface className="agent-account-dialog" labelledBy="agent-account-title" onClose={onClose} busy={busy}>
    <h2 id="agent-account-title" className="dialog-title">{t('agents.add')}</h2>
    <p className="dialog-message">{t('agentAccount.hint')}</p>
    {!selected ? <>
      {!supported && !error && <p className="dialog-message" role="status">{t('common.loading')}</p>}
      <div className="agent-account-choices">
        {supported?.filter(item => item.capabilities?.createAccount || item.capabilities?.streamBridge).map(item => <button className="choice-row" key={item.adapter} onClick={() => choose(item)}>
          {item.icon && <AgentMark path={item.icon} size={26} />}
          <span className="choice-text"><span className="choice-label">{item.label}</span><span className="choice-hint">{t(item.capabilities?.createAccount ? 'agentAccount.newAccount' : 'agentAccount.localCli')}</span></span>
          <IconPlus size={18} />
        </button>)}
      </div>
      {supported && !supported.some(item => item.capabilities?.createAccount || item.capabilities?.streamBridge) && <p className="dialog-message">{t('agentAccount.none')}</p>}
      <button className="ghost agent-account-discover" onClick={onDiscover}>{t('agentAccount.discover')}</button>
    </> : <>
      <div className="agent-account-selected">{selected.icon && <AgentMark path={selected.icon} size={24} />}<strong>{selected.label}</strong></div>
      <label className="dialog-field"><span>{t('agentAccount.name')}</span><input autoFocus value={label} placeholder={t('agentAccount.autoName')} onChange={event => setLabel(event.target.value)} maxLength={60} disabled={busy} onKeyDown={event => { if (event.key === 'Enter' && !event.nativeEvent.isComposing) void create(); }} /></label>
      {sources.length > 0 && <div className="agent-account-source"><span>{t(selected.capabilities?.createAccount ? 'agentAccount.copyFrom' : 'agentAccount.instance')}</span>
        <Popover label={t(selected.capabilities?.createAccount ? 'agentAccount.copyFrom' : 'agentAccount.instance')} triggerClassName="ghost" trigger={<>{sources.find(agent => agent.id === sourceId)?.label}<IconChevron size={14} open /></>} disabled={busy} placement="below">
          {close => sources.map(agent => <button className="pop-item compact" role="menuitemradio" aria-checked={sourceId === agent.id} key={agent.id} onClick={() => { setSourceId(agent.id); if (!selected.capabilities?.createAccount) { setLabel(agent.label); setStreamBridge(agent.streamBridgeEnabled ?? false); } close(); }}>{agent.label}</button>)}
        </Popover>
      </div>}
      {!selected.capabilities?.createAccount && selected.installGuide && (!sourceId || !sources.find(agent => agent.id === sourceId)?.available) && <p className="agent-cli-install"><code>{selected.installGuide}</code></p>}
      {selected.capabilities?.streamBridge && <StreamBridgeOption capability={selected.capabilities.streamBridge} value={streamBridge} onChange={setStreamBridge} disabled={busy} />}
      <p className="dialog-message agent-account-note">{t(selected.capabilities?.createAccount ? 'agentAccount.copyHint' : 'agentAccount.localHint')}</p>
    </>}
    {error && <p className="teams-error" role="alert">{error}</p>}
    <div className="dialog-actions">
      <button className="ghost" onClick={onClose} disabled={busy}>{t('common.cancel')}</button>
      {!supported && error && <button className="primary" onClick={() => void load()}>{t('agentLogin.retry')}</button>}
      {selected && <button className="primary" onClick={() => void create()} disabled={busy}>{t(busy ? selected.capabilities?.createAccount ? 'agentAccount.creating' : 'streamBridge.checking' : selected.capabilities?.createAccount ? 'agentAccount.create' : sourceId ? 'streamBridge.save' : 'agentAccount.connect')}</button>}
    </div>
  </DialogSurface>;
}
