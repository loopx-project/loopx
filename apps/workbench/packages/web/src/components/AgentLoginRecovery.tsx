import { useState } from 'react';
import { ApiError, type Api, type AgentInfo, type AgentLogin, type ModelOption } from '../api';
import { t } from '../i18n';
import { AgentLoginPanel } from './AgentLoginPanel';

export function needsAgentLogin(error: unknown): boolean {
  return error instanceof ApiError && error.details.code === 'agent_login_required';
}

/** A signed-out catalog is recoverable in place, without replacing the workspace or its draft. */
export function AgentLoginRecovery({ api, agent, onRecovered }: {
  api: Api; agent: Pick<AgentInfo, 'id' | 'label'>; onRecovered: (models: ModelOption[]) => void;
}) {
  const [login, setLogin] = useState<AgentLogin | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [signedIn, setSignedIn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const start = async () => {
    setBusy(true); setError('');
    try {
      setLogin(await api.startAgentLogin(agent.id));
      setExpanded(true);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const reload = async () => {
    setSignedIn(true); setBusy(true); setError('');
    try { onRecovered(await api.models(agent.id, true)); }
    catch (e) {
      if (needsAgentLogin(e)) { setSignedIn(false); setLogin(null); setExpanded(false); }
      else setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };
  return <section className="agent-login-recovery">
    <div className="agent-login-required" role="status">
      <span>{signedIn ? t('agentLogin.success') : t('agentLogin.required', agent.label)}</span>
      {!signedIn && !expanded && <button type="button" className="ghost" disabled={busy} onClick={() => void start()}>{t(login ? 'agentLogin.resume' : 'agentLogin.go')}</button>}
    </div>
    {error && <p className="teams-error" role="alert">{error} {signedIn && <button className="ghost" disabled={busy} onClick={() => void reload()}>{t('common.retry')}</button>}</p>}
    {login && expanded && <AgentLoginPanel key={login.id} api={api} agent={agent} initial={login}
      onState={setLogin} onReload={() => {}} onSuccess={() => void reload()}
      onCollapse={() => setExpanded(false)} onDismiss={() => { setLogin(null); setExpanded(false); }} />}
  </section>;
}
