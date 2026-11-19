import type { QuotaBalance, QuotaWindow } from '../api';
import { formatDateTime, t } from '../i18n';
import { balanceEmpty, formatReset, periodName, quotaGroups, quotaLeft, quotaSlots } from '../quota';
import type { QuotaReading } from '../quota-reading';
import { IconChevron } from './icons';

export const quotaWindowName = (window: QuotaWindow) => [window.scope, periodName(window.label)].filter(Boolean).join(' · ');
export const quotaStale = (reading: QuotaReading) => Boolean(reading.failedAt || reading.quota && Date.now() - Date.parse(reading.quota.checkedAt) > 60_000);

export function QuotaMeter({ window, stale = false, detail = false, compact = false, summary = false }: { window: QuotaWindow; stale?: boolean; detail?: boolean; compact?: boolean; summary?: boolean }) {
  const remaining = quotaLeft(window);
  return <div className={`quota-meter${stale ? ' stale' : remaining === 0 ? ' exhausted' : remaining <= 20 ? ' low' : ''}`}
    role="meter" aria-label={quotaWindowName(window)} aria-valuemin={0} aria-valuemax={100} aria-valuenow={remaining}
    aria-valuetext={`${stale ? `${t('quota.cachedReading')} · ` : ''}${t('quota.remaining', remaining)}`}>
    <div className="quota-meter-label"><span>{compact ? periodName(window.label) : quotaWindowName(window)}</span><strong className="quota-percent">{stale && !compact && <small>{t('quota.cachedReading')} · </small>}{compact ? t('quota.percent', remaining) : t('quota.remaining', remaining)}</strong></div>
    <div className="quota-meter-track" aria-hidden="true"><div style={{ width: `${remaining}%` }} /></div>
    {!summary && <span className="quota-meter-reset">{formatReset(window) || t('quota.resetUnknown')}</span>}
    {detail && <span className="quota-meter-used">{t('quota.board.used', window.usedPercent)}</span>}
    {!summary && window.loadPercent !== undefined && (detail || window.loadPercent >= 100) && <span className={`quota-load${window.loadPercent >= 100 ? ' high' : ''}`}>{t('quota.load', window.loadPercent)}</span>}
  </div>;
}

export function QuotaPair({ windows, stale = false, summary = false }: { windows: QuotaWindow[]; stale?: boolean; summary?: boolean }) {
  const slots = quotaSlots(windows);
  return <div className="quota-pair">{(summary ? slots.slice(0, 2) : slots).map(({ label, window }, index) => window
    ? <QuotaMeter key={`${label}:${index}`} window={window} stale={stale} compact summary={summary} />
    : <div key={label} className="quota-missing"><div className="quota-meter-label"><span>{periodName(label)}</span><span>{t('quota.notReported')}</span></div><div className="quota-missing-track" /></div>)}</div>;
}

/** Pay-as-you-go balances in one line: the amount as reported, coloured only once it runs out. */
export function QuotaBalances({ balances, stale = false }: { balances?: QuotaBalance[]; stale?: boolean }) {
  if (!balances?.length) return null;
  return <div className={`quota-balances${stale ? ' stale' : ''}`}>{balances.map((balance, index) =>
    <span key={`${balance.scope}:${index}`} className={`quota-balance${balanceEmpty(balance) ? ' exhausted' : ''}`}
      aria-label={`${balance.scope} · ${t('quota.balance')} ${balance.amount}${stale ? ` · ${t('quota.cachedReading')}` : ''}`}>
      <span>{balance.scope}</span><strong>{balance.amount}</strong>
    </span>)}</div>;
}

/** Match a reading's footprint without presenting invented values or periods. */
function QuotaPlaceholder({ detail = false, summary = false }: { detail?: boolean; summary?: boolean }) {
  return <div className="quota-meter quota-placeholder" aria-hidden="true">
    <div className="quota-meter-label"><span>{'\u00a0'}</span><strong className="quota-percent">{'\u00a0'}</strong></div>
    <div className="quota-meter-track" />
    {!summary && <span className="quota-meter-reset">{'\u00a0'}</span>}
    {detail && <span className="quota-meter-used">{'\u00a0'}</span>}
  </div>;
}

/** One model pool is visible immediately; other pools retain their own periods and values. */
export function QuotaPreview({ reading, summary = false }: { reading: QuotaReading; summary?: boolean }) {
  const groups = quotaGroups(reading.quota?.windows ?? []);
  const stale = quotaStale(reading);
  if (summary) return <div className="quota-preview compact" aria-busy={reading.loading}>
    {groups[0] ? <QuotaPair windows={groups[0].windows} stale={stale} summary />
      : reading.quota?.balances?.length ? null
      : !reading.quota && !reading.error ? <div className="quota-pair"><QuotaPlaceholder summary /><QuotaPlaceholder summary /></div>
        : <p className="quota-preview-empty">{t(reading.error ? 'quota.unavailable' : 'quota.empty')}</p>}
    <QuotaBalances balances={reading.quota?.balances} stale={stale} />
  </div>;
  return <div className="quota-preview" aria-busy={reading.loading}>
    {!reading.quota && !reading.error && <div className="quota-pair"><QuotaPlaceholder /><QuotaPlaceholder /></div>}
    {groups[0]?.scope && <div className="quota-pool-name">{groups[0].scope}</div>}
    {groups[0] && <QuotaPair windows={groups[0].windows} stale={stale} />}
    <QuotaBalances balances={reading.quota?.balances} stale={stale} />
    <QuotaReadingStatus reading={reading} />
    {groups.length > 1 && <details className="quota-pools"><summary><IconChevron size={12} />{t('quota.morePools', groups.length - 1)}</summary>
      {groups.slice(1).map((group, index) => <div className="quota-pool" key={index}><div className="quota-pool-name">{group.scope}</div><QuotaPair windows={group.windows} stale={stale} /></div>)}
    </details>}
  </div>;
}

export function QuotaReadingStatus({ reading, detail = false }: { reading: QuotaReading; detail?: boolean }) {
  const { quota, error } = reading;
  const stale = quotaStale(reading);
  const constrained = quota?.windows.filter(window => window.usedPercent >= 80).length ?? 0;
  const exhausted = quota?.windows.some(window => window.usedPercent >= 100);
  const status = error ? quota?.windows.length || quota?.balances?.length ? t('quota.cached') : t('quota.unavailable')
    : !quota ? t('quota.loading') : !quota.windows.length && !quota.balances?.length ? t('quota.empty') : stale ? t('quota.cachedReading') : '';
  return <div className={`quota-reading-status${error || constrained ? ' attention' : ''}${!quota && !error && !detail ? ' sr-only' : ''}`} role="status">
    {status && <span>{status}</span>}
    {!stale && exhausted ? <span>{t('quota.exhausted')}</span> : !stale && constrained > 0 ? <span>{t('quota.lowCount', constrained)}</span> : null}
    {quota?.windows.some(window => (window.loadPercent ?? 0) >= 100) && <span>{t('quota.board.busy')}</span>}
    {detail && quota && <span>{t('quota.updatedAt', formatDateTime(new Date(quota.checkedAt), { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }))}</span>}
    {detail && error && <span className="quota-read-error">{error}</span>}
  </div>;
}

/** The same per-window detail is used in a chat quick look and the account page. */
export function QuotaWindows({ reading, detail = false }: { reading: QuotaReading; detail?: boolean }) {
  return <div className="quota-windows" aria-busy={reading.loading}>
    <QuotaReadingStatus reading={reading} detail />
    {!reading.quota && !reading.error && <><QuotaPlaceholder detail={detail} /><QuotaPlaceholder detail={detail} /></>}
    {reading.quota?.windows.map((window, index) => <QuotaMeter key={`${window.label}:${window.scope ?? ''}:${index}`} window={window} stale={quotaStale(reading)} detail={detail} />)}
    {reading.quota?.balances?.map((balance, index) => <div key={`${balance.scope}:${index}`} className={`quota-balance-row${balanceEmpty(balance) ? ' exhausted' : ''}${quotaStale(reading) ? ' stale' : ''}`}>
      <span>{balance.scope} · {t('quota.balance')}</span><strong>{balance.amount}</strong>
    </div>)}
    {reading.quota?.note && <p className="quota-note">{reading.quota.note}</p>}
  </div>;
}
