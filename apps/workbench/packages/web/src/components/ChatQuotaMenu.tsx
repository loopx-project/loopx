import type { Api } from '../api';
import { t } from '../i18n';
import { useAgentQuota, useQuotaNavigation } from '../quota-reading';
import { quotaGroups } from '../quota';
import { QuotaPreview, quotaStale } from './QuotaReadout';
import { IconChevron, IconRefresh } from './icons';

/** Mounted inside the open thread menu, so there is no second quota panel to drill into. */
export function ChatQuotaMenu({ api, agent, label, close }: { api: Api; agent: string; label: string; close: () => void }) {
  const reading = useAgentQuota(api, agent);
  const open = useQuotaNavigation();
  const windows = reading.quota?.windows ?? [];
  const stale = quotaStale(reading);
  const low = windows.filter(window => window.usedPercent >= 80).length;
  const exhausted = windows.some(window => window.usedPercent >= 100);
  const busy = windows.some(window => (window.loadPercent ?? 0) >= 100);
  const status = reading.error ? t(reading.quota ? 'quota.menuCached' : 'quota.unavailable')
    : !reading.quota ? t('quota.loading') : stale ? t('quota.cachedReading')
      : exhausted ? t('quota.exhausted') : low ? t('quota.menuLowCount', low) : '';
  const headline = [status, busy ? t('quota.board.busy') : ''].filter(Boolean).join(' · ') || t('app.quota');
  const caption = [quotaGroups(windows)[0]?.scope, label, reading.quota?.plan?.label].filter(Boolean).join(' · ');
  return <section className="quota-menu-preview" aria-label={t('quota.quickTitle')}>
    <div className={`quota-menu-head${reading.error || !stale && (low || busy) ? ' attention' : ''}`}>
      <button type="button" role="menuitem" className="quota-menu-open" aria-label={t('quota.all')} disabled={!open} onClick={() => { close(); open?.(); }}>
        <strong role="status">{headline}</strong><span title={caption}>{caption}</span><IconChevron size={12} />
      </button>
      <button type="button" role="menuitem" className="icon-btn" aria-busy={reading.loading} aria-disabled={reading.loading} aria-label={t('quota.refreshAgent', label)} onClick={() => { if (!reading.loading) void reading.refresh(); }}><IconRefresh size={14} /></button>
    </div>
    <QuotaPreview reading={reading} summary />
  </section>;
}
