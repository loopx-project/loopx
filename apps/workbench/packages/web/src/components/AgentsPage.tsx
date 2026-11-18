import { useMemo, useState } from 'react';
import { ApiError, type AgentInfo, type AgentLogin, type AgentReferences, type Api } from '../api';
import { AgentAccountDialog } from './AgentAccountDialog';
import { AgentLoginDialog } from './AgentLoginDialog';
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
  const [signingIn, setSigningIn] = useState<{ agent: AgentInfo; login: AgentLogin } | null>(null);
  const [settings, setSettings] = useState<AgentInfo | null>(null);
  const [renaming, setRenaming] = useState<AgentInfo | null>(null);
  const [removing, setRemoving] = useState<{ agent: AgentInfo; references: AgentReferences } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const groups = useMemo(() => groupsOf(agents), [agents]);

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
    setBusy(agent.id);
    try {
      if (agent.login === 'wizard') setSigningIn({ agent, login: await api.startAgentLogin(agent.id) });
      else await onLogin(agent);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
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
              {group.agents.map((agent) => (
                <article key={agent.id} className="agents-row">
                  <span className="agents-mark" aria-hidden="true">
                    {agent.icon ? <AgentMark path={agent.icon} size={18} /> : <IconAgents size={18} />}
                  </span>
                  <div className="agents-row-main">
                    <div className="agents-row-name">
                      <strong>{agent.label}</strong>
                      {agent.hidden && <span className="agents-hidden">{t('agents.hidden')}</span>}
                      {!agent.available && <span className="agents-unavailable">{t('agents.unavailable')}</span>}
                    </div>
                    <span className="agents-row-meta">
                      {agent.account === null ? t('agentAccount.needsLogin') : agent.account ?? t('agents.localAccount')}
                      {(agent.account || agent.id !== agent.label) && <span className="agents-row-id"> · {agent.id}</span>}
                    </span>
                  </div>
                  <div className="agents-row-actions">
                    {agent.login && (
                      <button
                        type="button"
                        className="ghost agents-login"
                        disabled={busy === agent.id}
                        onClick={() => void login(agent)}
                      >
                        <IconSignIn size={15} />
                        {t('agents.login')}
                      </button>
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
              ))}
            </section>
          ))}
        </div>
      )}

      {adding && <AgentAccountDialog api={api} agents={agents} onClose={() => setAdding(false)} onDiscover={() => { setAdding(false); onAdd(); }} onCreated={async () => { setAdding(false); try { await onReload(); } catch (e) { fail(e); } }} />}
      {settings && <AgentSettingsDialog api={api} agent={settings} onReload={onReload} onClose={() => setSettings(null)} />}
      {signingIn && <AgentLoginDialog api={api} agent={signingIn.agent} initial={signingIn.login} onReload={onReload} onClose={() => setSigningIn(null)} />}

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
