import { useEffect, useRef, useState } from 'react';
import type { AgentInfo, Api } from '../api';
import { t } from '../i18n';
import { DialogSurface } from './Dialog';
import { StreamBridgeOption } from './StreamBridgeOption';

/** Hub-owned runtime choices apply on the next turn, never interrupt a running native task. */
export function AgentSettingsDialog({ api, agent, onReload, onClose }: {
  api: Api; agent: AgentInfo; onReload: () => Promise<void> | void; onClose: () => void;
}) {
  const [enabled, setEnabled] = useState(agent.streamBridgeEnabled ?? false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  /** Why the bridge would not run on this install; empty once the check says it would. */
  const [incompatible, setIncompatible] = useState('');
  const checkId = useRef(0);
  const capability = agent.capabilities.streamBridge;
  // Advisory only: saving still succeeds and turns fall back to text output on their own.
  const checkBridge = (on: boolean) => {
    const id = ++checkId.current;
    if (!capability || !on) { setIncompatible(''); return; }
    void api.agentStreamBridge(agent.id)
      .then(status => { if (checkId.current === id) setIncompatible(status.compatible || !status.reason ? '' : status.reason); })
      .catch(() => { if (checkId.current === id) setIncompatible(''); });
  };
  useEffect(() => { checkBridge(agent.streamBridgeEnabled ?? false); }, []);
  const toggle = (next: boolean) => { setEnabled(next); checkBridge(next); };
  const save = async () => {
    if (busy) return;
    setBusy(true); setError('');
    try {
      await api.updateAgent(agent.id, { streamBridge: enabled });
      await onReload();
      onClose();
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  };
  return <DialogSurface className="agent-settings-dialog" labelledBy="agent-settings-title" onClose={onClose} busy={busy}>
    <h2 id="agent-settings-title" className="dialog-title">{t('agents.settings')}</h2>
    <p className="dialog-message">{agent.label}</p>
    {capability && <StreamBridgeOption capability={capability} value={enabled} onChange={toggle} disabled={busy} />}
    {incompatible && <p className="agent-bridge-hint" role="status">{t('streamBridge.incompatible', incompatible)}</p>}
    <p className="agent-bridge-hint">{t('streamBridge.nextTurn')}</p>
    {error && <p className="teams-error" role="alert">{error}</p>}
    <div className="dialog-actions">
      <button type="button" className="ghost" disabled={busy} onClick={onClose}>{t('common.cancel')}</button>
      <button type="button" className="primary" disabled={busy || enabled === (agent.streamBridgeEnabled ?? false)} onClick={() => void save()}>{t(busy ? 'streamBridge.checking' : 'streamBridge.save')}</button>
    </div>
  </DialogSurface>;
}
