import { useEffect, useRef, useState } from 'react';
import { ApiError, type AgentInfo, type AgentLogin, type Api } from '../api';
import { copyText } from '../clipboard';
import { t } from '../i18n';
import { IconCheck, IconCopy, IconExternal } from './icons';

/**
 * Inline sign-in panel rendered directly under an agent row so the rest of the workspace
 * (including the remote desktop pane) stays interactive. Collapsing or leaving the page keeps
 * the daemon login session alive; only the explicit cancel action terminates it.
 */
export function AgentLoginPanel({ api, agent, initial, onState, onReload, onCollapse, onDismiss, onSuccess }: {
  api: Api;
  agent: Pick<AgentInfo, 'id' | 'label'>;
  initial: AgentLogin;
  onState: (next: AgentLogin) => void;
  onReload: () => Promise<void> | void;
  onCollapse: () => void;
  onDismiss: () => void;
  onSuccess?: () => void;
}) {
  const [login, setLogin] = useState(initial);
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const reload = useRef(onReload); reload.current = onReload;
  const notify = useRef(onState); notify.current = onState;
  const success = useRef(onSuccess); success.current = onSuccess;
  const active = ['starting', 'waiting', 'submitting'].includes(login.status);
  const codeOrUrl = login.input === 'code-or-url';

  const update = (next: AgentLogin) => {
    setLogin(next);
    notify.current(next);
  };

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await api.agentLogin(agent.id, login.id);
        if (!alive) return;
        update(next);
        setError('');
        if (next.status === 'success') { await reload.current(); success.current?.(); }
        if (['starting', 'waiting', 'submitting'].includes(next.status)) timer = setTimeout(poll, 1000);
      } catch (e) {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 404) {
          update({ id: login.id, status: 'error', error: 'expired' });
          void reload.current();
          return;
        }
        setError(e instanceof Error ? e.message : String(e));
        timer = setTimeout(poll, 2500);
      }
    };
    if (active) void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [api, agent.id, login.id, active]);

  const restart = async () => {
    setBusy(true); setError(''); setCode(''); setCopied(false);
    try {
      const started = await api.startAgentLogin(agent.id);
      update(started);
      void reload.current();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    if (busy || !code.trim()) return;
    setBusy(true); setError('');
    try {
      update(await api.submitAgentLogin(agent.id, login.id, code.trim()));
      setCode('');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (busy) return;
    setBusy(true); setError('');
    try {
      if (active) await api.cancelAgentLogin(agent.id, login.id);
      onDismiss();
      void reload.current();
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        onDismiss();
        void reload.current();
      } else {
        setError(e instanceof Error ? e.message : String(e));
        setBusy(false);
      }
    }
  };

  return (
    <div className="agent-login-panel" role="region" aria-label={t('agentLogin.title', agent.label)}>
      <p className="agent-login-hint">{t(codeOrUrl ? 'agentLogin.hintCodeOrUrl' : 'agentLogin.hint')}</p>
      {login.status === 'waiting' && login.url && (
        <>
          <div className="agent-login-link">
            <a className="primary" href={login.url} target="_blank" rel="noopener noreferrer">
              <IconExternal size={16} />{t('agentLogin.open')}
            </a>
            <button
              type="button"
              className="ghost"
              onClick={async () => {
                if (await copyText(login.url!)) setCopied(true);
                else setError(t('agentLogin.copyFailed'));
              }}
            >
              {copied ? <IconCheck size={16} /> : <IconCopy size={16} />}
              {t(copied ? 'agentLogin.copied' : 'agentLogin.copy')}
            </button>
          </div>
          <div className="dialog-field agent-login-code">
            <label htmlFor={`agent-login-code-${agent.id}`}>{t(codeOrUrl ? 'agentLogin.codeOrUrl' : 'agentLogin.code')}</label>
            <div className="agent-login-code-row">
              <input
                id={`agent-login-code-${agent.id}`}
                value={code}
                placeholder={t(codeOrUrl ? 'agentLogin.codeOrUrl' : 'agentLogin.codeHint')}
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                onChange={(event) => setCode(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.nativeEvent.isComposing) void submit();
                }}
              />
              <button type="button" className="primary" disabled={busy || !code.trim()} onClick={() => void submit()}>
                {t('agentLogin.submit')}
              </button>
            </div>
          </div>
        </>
      )}
      {login.status !== 'waiting' && (
        <p className={`agent-login-status${login.status === 'error' ? ' teams-error' : ''}`} role={login.status === 'error' ? 'alert' : 'status'}>
          {login.status === 'success' && <IconCheck size={20} />}
          {login.status === 'error' ? t(`agentLogin.${login.error ?? 'failed'}`) : t(`agentLogin.${login.status}`)}
        </p>
      )}
      {error && <p className="teams-error" role="alert">{error}</p>}
      <div className="agent-login-foot">
        {active ? (
          <>
            <span className="agent-login-keep">{t('agentLogin.keepHint')}</span>
            <div className="agent-login-foot-actions">
              <button type="button" className="ghost" disabled={busy} onClick={onCollapse}>
                {t('agentLogin.collapse')}
              </button>
              <button type="button" className="ghost danger" disabled={busy} onClick={() => void cancel()}>
                {t('agentLogin.cancel')}
              </button>
            </div>
          </>
        ) : (
          <div className="agent-login-foot-actions">
            <button type="button" className="ghost" disabled={busy} onClick={onDismiss}>
              {t('common.close')}
            </button>
            {(login.status === 'error' || login.status === 'cancelled') && (
              <button type="button" className="primary" disabled={busy} onClick={() => void restart()}>
                {t('agentLogin.retry')}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
