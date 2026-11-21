import { useEffect, useRef, useState } from 'react';
import { ApiError, type AgentInfo, type AgentLogin, type Api } from '../api';
import { copyText } from '../clipboard';
import { t } from '../i18n';
import { DialogSurface } from './Dialog';
import { AgentMark, IconCheck, IconCopy, IconExternal } from './icons';

export function AgentLoginDialog({ api, agent, initial, onReload, onClose }: {
  api: Api; agent: AgentInfo; initial: AgentLogin; onReload: () => Promise<void> | void; onClose: () => void;
}) {
  const [login, setLogin] = useState(initial);
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const reload = useRef(onReload); reload.current = onReload;
  const active = ['starting', 'waiting', 'submitting'].includes(login.status);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await api.agentLogin(agent.id, login.id);
        if (!alive) return;
        setLogin(next); setError('');
        if (next.status === 'success') await reload.current();
        if (['starting', 'waiting', 'submitting'].includes(next.status)) timer = setTimeout(poll, 1000);
      } catch (e) {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 404) {
          setLogin(current => ({ id: current.id, status: 'error', error: 'expired' }));
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
    try { setLogin(await api.startAgentLogin(agent.id)); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const submit = async () => {
    if (busy || !code.trim()) return;
    setBusy(true); setError('');
    try { setLogin(await api.submitAgentLogin(agent.id, login.id, code.trim())); setCode(''); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const close = async () => {
    if (busy) return;
    setBusy(true);
    try {
      if (active) await api.cancelAgentLogin(agent.id, login.id);
      onClose();
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) onClose();
      else { setError(e instanceof Error ? e.message : String(e)); setBusy(false); }
    }
  };
  return <DialogSurface className="agent-login-dialog" labelledBy="agent-login-title" onClose={() => void close()} busy={busy}>
    <div className="agent-login-heading">{agent.icon && <AgentMark path={agent.icon} size={26} />}<div><h2 id="agent-login-title" className="dialog-title">{t('agentLogin.title', agent.label)}</h2><p className="dialog-message">{t('agentLogin.hint')}</p></div></div>
    {login.status === 'waiting' && login.url && <>
      <div className="agent-login-link"><a className="primary" href={login.url} target="_blank" rel="noopener noreferrer"><IconExternal size={16} />{t('agentLogin.open')}</a><button className="ghost" onClick={async () => { if (await copyText(login.url!)) setCopied(true); else setError(t('agentLogin.copyFailed')); }}>{copied ? <IconCheck size={16} /> : <IconCopy size={16} />}{t(copied ? 'agentLogin.copied' : 'agentLogin.copy')}</button></div>
      <label className="dialog-field"><span>{t('agentLogin.code')}</span><input autoFocus value={code} placeholder={t('agentLogin.codeHint')} autoComplete="off" autoCapitalize="none" spellCheck={false} onChange={event => setCode(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.nativeEvent.isComposing) void submit(); }} /></label>
    </>}
    {login.status !== 'waiting' && <p className={`agent-login-status${login.status === 'error' ? ' teams-error' : ''}`} role={login.status === 'error' ? 'alert' : 'status'}>{login.status === 'success' && <IconCheck size={20} />}{login.status === 'error' ? t(`agentLogin.${login.error ?? 'failed'}`) : t(`agentLogin.${login.status}`)}</p>}
    {error && <p className="teams-error" role="alert">{error}</p>}
    <div className="dialog-actions"><button className="ghost" onClick={() => void close()} disabled={busy}>{t(active ? 'common.cancel' : 'common.close')}</button>
      {login.status === 'waiting' && <button className="primary" disabled={busy || !code.trim()} onClick={() => void submit()}>{t('agentLogin.submit')}</button>}
      {(login.status === 'error' || login.status === 'cancelled') && <button className="primary" disabled={busy} onClick={() => void restart()}>{t('agentLogin.retry')}</button>}
    </div>
  </DialogSurface>;
}
