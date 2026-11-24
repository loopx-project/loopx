import { useEffect, useRef, useState } from 'react';
import { ApiError, type Api, type AgentInfo, type AgentLogin, type ModelOption } from '../api';
import { t } from '../i18n';
import { AgentLoginPanel } from './AgentLoginPanel';

export function needsAgentLogin(error: unknown): boolean {
  return error instanceof ApiError && error.details.code === 'agent_login_required';
}

/** A signed-out catalog is recoverable in place, without replacing the workspace or its draft. */
export function AgentLoginRecovery({ api, agent, empty = false, onRecovered, onTerminalLogin }: {
  api: Api; agent: Pick<AgentInfo, 'id' | 'label' | 'login'>; onRecovered: (models: ModelOption[]) => void;
  empty?: boolean;
  onTerminalLogin?: () => Promise<void>;
}) {
  const [login, setLogin] = useState<AgentLogin | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [signedIn, setSignedIn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [terminalStarted, setTerminalStarted] = useState(false);
  const recoveredRef = useRef(onRecovered); recoveredRef.current = onRecovered;
  // Terminal CLIs own their authentication. Read back that account; never assume opening it logged in.
  useEffect(() => {
    if (!terminalStarted) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const deadline = Date.now() + 10 * 60_000;
    const check = async () => {
      if (Date.now() >= deadline) { setTerminalStarted(false); return; }
      try {
        const models = await api.models(agent.id, true);
        if (live) {
          if (!models.length) { setError(t('agentLogin.noModels')); setTerminalStarted(false); }
          else recoveredRef.current(models);
        }
      } catch (e) {
        if (!live) return;
        if (!needsAgentLogin(e)) {
          setError(e instanceof Error ? e.message : String(e));
          setTerminalStarted(false);
          return;
        }
        timer = setTimeout(check, 2000);
      }
    };
    void check();
    return () => { live = false; clearTimeout(timer); };
  }, [api, agent.id, terminalStarted]);
  const start = async () => {
    setBusy(true); setError('');
    try {
      if (agent.login === 'terminal') {
        await onTerminalLogin?.();
        setTerminalStarted(true);
        return;
      }
      setLogin(await api.startAgentLogin(agent.id));
      setExpanded(true);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const reload = async (verified = signedIn) => {
    setSignedIn(verified); setBusy(true); setError('');
    try {
      const models = await api.models(agent.id, true);
      if (models.length) onRecovered(models);
      else setError(t('agentLogin.noModels'));
    }
    catch (e) {
      if (needsAgentLogin(e)) { setSignedIn(false); setLogin(null); setExpanded(false); }
      else setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };
  return <section className="agent-login-recovery">
    <div className="agent-login-required" role="status">
      <span>{signedIn ? t('agentLogin.success') : t(empty ? 'agentLogin.empty' : 'agentLogin.required', agent.label)}</span>
      {!signedIn && !expanded && agent.login && <button type="button" className="ghost" disabled={busy} onClick={() => void start()}>{t(login ? 'agentLogin.resume' : 'agentLogin.go')}</button>}
      {agent.login !== 'wizard' && <button type="button" className="ghost" disabled={busy} onClick={() => void reload()}>{t('common.retry')}</button>}
    </div>
    {agent.login !== 'wizard' && <p className="muted">{t('agentLogin.nativeHint')}</p>}
    {error && <p className="teams-error" role="alert">{error} {signedIn && <button className="ghost" disabled={busy} onClick={() => void reload()}>{t('common.retry')}</button>}</p>}
    {login && expanded && <AgentLoginPanel key={login.id} api={api} agent={agent} initial={login}
      onState={setLogin} onReload={() => {}} onSuccess={() => void reload(true)}
      onCollapse={() => setExpanded(false)} onDismiss={() => { setLogin(null); setExpanded(false); }} />}
  </section>;
}
