import { useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, type AgentInfo, type AgentLogin, type AgentReferences, type Api } from '../api';
import { AgentAccountDialog } from './AgentAccountDialog';
import { AgentLoginPanel } from './AgentLoginPanel';
import { AgentSettingsDialog } from './AgentSettingsDialog';
import { t } from '../i18n';
import { Dialog } from './Dialog';
import { Popover } from './Popover';
import { AgentMark, IconAgents, IconArchive, IconEdit, IconMore, IconPlus, IconRestore, IconSignIn, IconSettings, IconTrash } from './icons';

interface Props {
  api: Api;
  agents: AgentInfo[];
  onAdd: () => void;
  onReload: () => Promise<void> | void;
  onLogin: (agent: AgentInfo) => Promise<void> | void;
}

/** Groups instances by adapter; the shortest label names the group, as the picker does. */
function groupsOf(agents: AgentInfo[]): Array<{ key: string; label: string; agents: AgentInfo[] }> {
  const groups = new Map<string, AgentInfo[]>();
  for (const agent of agents) {
    const key = agent.adapter ?? `_${agent.id}`;
    groups.set(key, [...(groups.get(key) ?? []), agent]);
  }
  return [...groups.entries()].map(([key, list]) => ({
    key,
    label: list.reduce((short, a) => (a.label.length < short.length ? a.label : short), list[0].label),
    agents: list,
  }));
}

const total = (references: AgentReferences): number =>
  references.threads + references.schedules + references.teams + references.handoverOrders;

/**
 * The page for the instances themselves: see what is signed in, rename it, start its login again,
 * or take it out of the hub. Removing never touches the agent's own config, login or sessions.
 */
export function AgentsPage({ api, agents, onAdd, onReload, onLogin }: Props) {
  const [adding, setAdding] = useState(false);
  const [logins, setLogins] = useState<Record<string, AgentLogin>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [settings, setSettings] = useState<AgentInfo | null>(null);
  const [renaming, setRenaming] = useState<AgentInfo | null>(null);
  const [removing, setRemoving] = useState<{ agent: AgentInfo; references: AgentReferences } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const groups = useMemo(() => groupsOf(agents), [agents]);
  const reloadRef = useRef(onReload); reloadRef.current = onReload;

  useEffect(() => {
    void reloadRef.current();
  }, []);

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));

  const startRename = (agent: AgentInfo) => {
    setError(null);
    setRenaming(agent);
  };

  const rename = async (label: string) => {
    if (!renaming || !label.trim()) return;
    setBusy(renaming.id);
    setError(null);
    try {
      await api.updateAgent(renaming.id, { label: label.trim() });
      setRenaming(null);
      await onReload();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  };

  const toggleHidden = async (agent: AgentInfo) => {
    setError(null);
    setBusy(agent.id);
    try {
      await api.updateAgent(agent.id, { hidden: !agent.hidden });
      await onReload();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  };

  const askRemove = async (agent: AgentInfo) => {
    setError(null);
    setBusy(agent.id);
    try {
      setRemoving({ agent, references: await api.agentReferences(agent.id) });
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    if (!removing) return;
    setBusy(removing.agent.id);
    setError(null);
    try {
      await api.removeAgent(removing.agent.id);
      setRemoving(null);
      await onReload();
    } catch (e) {
      // A thread may have started between the reference check and this call; show the fresh count.
      if (e instanceof ApiError && e.status === 409 && e.details.references) {
        setRemoving({ agent: removing.agent, references: e.details.references as AgentReferences });
      } else {
        fail(e);
      }
    } finally {
      setBusy(null);
    }
  };

  const login = async (agent: AgentInfo) => {
    setError(null);
    if (agent.login === 'wizard') {
      const current = logins[agent.id] ?? agent.activeLogin;
      if (current && ['starting', 'waiting', 'submitting'].includes(current.status)) {
        setLogins((prev) => ({ ...prev, [agent.id]: current }));
        setExpanded((prev) => ({ ...prev, [agent.id]: true }));
        return;
      }
      setBusy(agent.id);
      try {
        const started = await api.startAgentLogin(agent.id);
        setLogins((prev) => ({ ...prev, [agent.id]: started }));
        setExpanded((prev) => ({ ...prev, [agent.id]: true }));
        void onReload();
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
      return;
    }
    setBusy(agent.id);
    try {
      await onLogin(agent);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  };

  const dismissLogin = (agentId: string) => {
    setExpanded((prev) => {
      const next = { ...prev };
      delete next[agentId];
      return next;
    });
    setLogins((prev) => {
      const next = { ...prev };
      delete next[agentId];
      return next;
    });
  };

  const created = async (accountId?: string) => {
    try {
      if (accountId) {
        const agent = (await api.agents()).find(agent => agent.id === accountId);
        if (agent?.login) await login(agent);
      }
    } catch (e) { fail(e); }
    finally {
      setAdding(false);
      try { await onReload(); } catch (e) { fail(e); }
    }
  };

  return (
    <section className="agents-page">
      <div className="agents-page-head">
        <div>
          <h1>{t('agents.title')}</h1>
          <p>{t('agents.intro')}</p>
        </div>
        <button type="button" className="primary agents-add" onClick={() => setAdding(true)}>
          <IconPlus size={16} />
          {t('agents.add')}
        </button>
      </div>

      {error && <div className="teams-error" role="alert">{error}</div>}

      {agents.length === 0 ? (
        <div className="agents-empty">
          <IconAgents size={32} />
          <p>{t('agents.empty')}</p>
        </div>
      ) : (
        <div className="agents-list">
          {groups.map((group) => (
            <section key={group.key} className="agents-group" aria-label={group.label}>
              {group.agents.length > 1 && <h2 className="agents-group-title">{group.label} · {group.agents.length}</h2>}
              {group.agents.map((agent) => {
                const activeLogin = logins[agent.id] ?? agent.activeLogin;
                const pendingLogin = !!activeLogin && ['starting', 'waiting', 'submitting'].includes(activeLogin.status);
                const isExpanded = !!expanded[agent.id] && !!activeLogin;
                return (
                <div key={agent.id} className="agents-row-wrap">
                <article className="agents-row">
                  <span className="agents-mark" aria-hidden="true">
                    {agent.icon ? <AgentMark path={agent.icon} size={18} /> : <IconAgents size={18} />}
                  </span>
                  <div className="agents-row-main">
                    <div className="agents-row-name">
                      <strong>{agent.label}</strong>
                      {agent.hidden && <span className="agents-hidden">{t('agents.hidden')}</span>}
                      {!agent.available && <span className="agents-unavailable">{t('agents.unavailable')}</span>}
                      {pendingLogin && (
                        <span className="agents-login-pending">
                          <span className="agents-login-dot" aria-hidden="true" />
                          {t('agentLogin.waitingBadge')}
                        </span>
                      )}
                    </div>
                    <span className="agents-row-meta">
                      {agent.account === null ? t('agentAccount.needsLogin') : agent.account ?? t('agents.localAccount')}
                      {(agent.account || agent.id !== agent.label) && <span className="agents-row-id"> · {agent.id}</span>}
                    </span>
                  </div>
                  <div className="agents-row-actions">
                    {agent.login && (
                      isExpanded ? (
                        <button
                          type="button"
                          className="ghost agents-login"
                          onClick={() => setExpanded((prev) => ({ ...prev, [agent.id]: false }))}
                        >
                          {t('agentLogin.collapse')}
                        </button>
                      ) : (
                        <button
                          type="button"
                          className={`${pendingLogin ? 'primary' : 'ghost'} agents-login`}
                          disabled={busy === agent.id}
                          onClick={() => void login(agent)}
                        >
                          {!pendingLogin && <IconSignIn size={15} />}
                          {t(pendingLogin ? 'agentLogin.resume' : 'agents.login')}
                        </button>
                      )
                    )}
                    <Popover
                      label={t('agents.actions')}
                      triggerLabel={t('agents.actions')}
                      trigger={<IconMore size={17} />}
                      triggerClassName="icon-btn"
                      disabled={busy === agent.id}
                      align="end"
                    >
                      {(close) => (
                        <>
                          {agent.capabilities.streamBridge && <button type="button" className="pop-item compact" role="menuitem" onClick={() => { close(); setSettings(agent); }}>
                            <IconSettings size={15} />{t('agents.settings')}
                          </button>}
                          <button
                            type="button"
                            className="pop-item compact"
                            role="menuitem"
                            onClick={() => {
                              close();
                              startRename(agent);
                            }}
                          >
                            <IconEdit size={15} />
                            {t('agents.rename')}
                          </button>
                          <button
                            type="button"
                            className="pop-item compact"
                            role="menuitem"
                            onClick={() => {
                              close();
                              void toggleHidden(agent);
                            }}
                          >
                            {agent.hidden ? <IconRestore size={15} /> : <IconArchive size={15} />}
                            {t(agent.hidden ? 'agents.unhide' : 'agents.hide')}
                          </button>
                          <button
                            type="button"
                            className="pop-item compact danger"
                            role="menuitem"
                            onClick={() => {
                              close();
                              void askRemove(agent);
                            }}
                          >
                            <IconTrash size={15} />
                            {t('agents.remove')}
                          </button>
                        </>
                      )}
                    </Popover>
                  </div>
                </article>
                {isExpanded && activeLogin && (
                  <AgentLoginPanel
                    key={activeLogin.id}
                    api={api}
                    agent={agent}
                    initial={activeLogin}
                    onState={(next) => setLogins((prev) => ({ ...prev, [agent.id]: next }))}
                    onReload={onReload}
                    onCollapse={() => setExpanded((prev) => ({ ...prev, [agent.id]: false }))}
                    onDismiss={() => dismissLogin(agent.id)}
                  />
                )}
                </div>
                );
              })}
            </section>
          ))}
        </div>
      )}

      {adding && <AgentAccountDialog api={api} agents={agents} onClose={() => setAdding(false)} onDiscover={() => { setAdding(false); onAdd(); }} onCreated={created} />}
      {settings && <AgentSettingsDialog api={api} agent={settings} onReload={onReload} onClose={() => setSettings(null)} />}

      {renaming && (
        <Dialog
          title={t('agents.renameTitle')}
          message={t('agents.renameHint')}
          input={{ initial: renaming.label, label: t('agents.renameLabel') }}
          confirmLabel={t('agents.rename')}
          confirmDisabled={busy === renaming.id}
          onCancel={() => setRenaming(null)}
          onConfirm={(value) => void rename(value ?? '')}
        >
          {error && <p className="teams-error" role="alert">{error}</p>}
        </Dialog>
      )}

      {removing && (
        <Dialog
          title={total(removing.references) > 0 ? t('agents.removeBlockedTitle') : t('agents.removeTitle')}
          message={
            total(removing.references) > 0
              ? t(
                  'agents.removeBlocked',
                  removing.references.threads,
                  removing.references.schedules,
                  removing.references.teams,
                  removing.references.handoverOrders,
                )
              : t('agents.removeHint')
          }
          confirmLabel={total(removing.references) > 0 ? t('common.close') : t('agents.remove')}
          danger={total(removing.references) === 0}
          hideCancel={total(removing.references) > 0}
          confirmDisabled={busy === removing.agent.id}
          onCancel={() => setRemoving(null)}
          onConfirm={() => {
            if (total(removing.references) > 0) setRemoving(null);
            else void remove();
          }}
        />
      )}
    </section>
  );
}
