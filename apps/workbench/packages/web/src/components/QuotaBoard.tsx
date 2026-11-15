import { useState } from 'react';
import type { AgentInfo, Api } from '../api';
import { t } from '../i18n';
import { quotaRows } from '../quota';
import { refreshQuota, useAgentQuota } from '../quota-reading';
import { SettingsSection } from './PageNavigation';
import { QuotaPreview, QuotaWindows, quotaStale } from './QuotaReadout';
import { AgentMark, IconChevron, IconRefresh, IconSearch } from './icons';

interface Props { api: Api; agents: AgentInfo[]; current?: string; account?: string; onAccount: (id: string) => void }

function AccountRow({ api, agent, current, onOpen, attentionOnly, search }: {
  api: Api; agent: AgentInfo; current: boolean; onOpen: () => void; attentionOnly: boolean; search: string;
}) {
  const reading = useAgentQuota(api, agent.id);
  const windows = reading.quota?.windows ?? [];
  const needsAttention = Boolean(reading.error || quotaStale(reading) || windows.some(window => window.usedPercent >= 80 || (window.loadPercent ?? 0) >= 100));
  if (attentionOnly && !needsAttention || search && !`${agent.label} ${reading.quota?.plan?.label ?? ''}`.toLocaleLowerCase().includes(search)) return null;
  return <div className="quota-account-row quota-board-row">
    <button type="button" className="quota-account-open" onClick={onOpen} aria-label={t('quota.accountDetails', agent.label)}>
    {agent.icon && <span className="agent-mark"><AgentMark path={agent.icon} size={20} /></span>}
    <span className="quota-account-name"><span className="quota-board-name">{agent.label}</span>{current && <small className="quota-current">{t('quota.current')}</small>}
      {reading.quota?.plan && <span className="quota-account-plan quota-plan" title={reading.quota.plan.id}>{reading.quota.plan.label}</span>}
    </span>
    <IconChevron size={15} />
    </button>
    <div className="quota-account-summary"><QuotaPreview reading={reading} /></div>
  </div>;
}

function AccountDetail({ api, agent, current }: { api: Api; agent: AgentInfo; current: boolean }) {
  const reading = useAgentQuota(api, agent.id);
  return <SettingsSection title={agent.label} className="quota-page quota-account-detail">
    <div className="quota-account-meta">{reading.quota?.plan && <span title={reading.quota.plan.id}>{reading.quota.plan.label}</span>}{current && <span>{t('quota.current')}</span>}</div>
    <QuotaWindows reading={reading} detail />
    <button type="button" className="text-btn quota-detail-refresh" aria-busy={reading.loading} disabled={reading.loading} onClick={() => void reading.refresh()}><IconRefresh size={14} />{reading.error ? t('common.retry') : t('quota.refreshAgent', agent.label)}</button>
  </SettingsSection>;
}

export function QuotaBoard({ api, agents, current, account, onAccount }: Props) {
  const [search, setSearch] = useState('');
  const [attentionOnly, setAttentionOnly] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const rows = quotaRows(agents, current);
  const agent = rows.find(agent => agent.id === account);
  const refreshAll = async () => {
    setRefreshing(true);
    await Promise.allSettled(rows.map(agent => refreshQuota(api, agent.id)));
    setRefreshing(false);
  };
  if (account) return agent ? <AccountDetail key={agent.id} api={api} agent={agent} current={current === agent.id} /> : <SettingsSection title={t('app.quota')}><p role="status">{t('quota.noAgents')}</p></SettingsSection>;
  return <SettingsSection title={t('app.quota')} className="quota-page">
    <div className="quota-page-toolbar"><p className="settings-hint">{t('quota.accounts', rows.length)}</p><button type="button" className="text-btn strong" aria-busy={refreshing} disabled={refreshing} onClick={() => void refreshAll()}><IconRefresh size={14} />{t('quota.refreshAll')}</button></div>
    {rows.length > 0 && <>
      <div className="quota-page-filters"><div className="quota-page-tabs" role="group" aria-label={t('quota.filter')}>
        <button type="button" aria-pressed={!attentionOnly} onClick={() => setAttentionOnly(false)}>{t('settings.all')}</button>
        <button type="button" aria-pressed={attentionOnly} onClick={() => setAttentionOnly(true)}>{t('quota.attention')}</button>
      </div><label className="panel-search"><IconSearch size={15} /><input type="search" aria-label={t('quota.search')} placeholder={t('quota.search')} value={search} onChange={event => setSearch(event.target.value)} /></label></div>
      <div className="quota-account-list">{rows.map(agent => <AccountRow key={agent.id} api={api} agent={agent} current={agent.id === current} onOpen={() => onAccount(agent.id)} attentionOnly={attentionOnly} search={search.trim().toLocaleLowerCase()} />)}<p className="quota-no-matches">{t('quota.noMatches')}</p></div>
    </>}
    {rows.length === 0 && <p role="status">{t('quota.noAgents')}</p>}
    <p className="quota-page-note">{t('quota.overviewHint')}</p>
  </SettingsSection>;
}
