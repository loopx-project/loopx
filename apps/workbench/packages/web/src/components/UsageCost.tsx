import { useEffect, useId, useState } from 'react';
import { createPortal } from 'react-dom';
import type { Api } from '../api';
import type { CostRange, CostReport, CostSummary, PriceRates } from '../observability';
import { formatNumber, t } from '../i18n';
import { DialogSurface } from './Dialog';
import { IconClose } from './icons';

const money = (n: number) => formatNumber(n, { minimumFractionDigits: 2, maximumFractionDigits: n > 0 && n < .01 ? 4 : 2 });
const range = (currency: string, value: CostRange) => `${currency} ${money(value.amount)}${money(value.amount) !== money(value.maxAmount) ? `–${money(value.maxAmount)}` : ''}`;
const totals = (values: Record<string, CostRange>) => Object.entries(values).map(([currency, value]) => range(currency, value)).join(' · ') || t('common.unknown');

function Rates({ rates, currency }: { rates: PriceRates; currency: string }) {
  return <dl className="obs-price-rates">
    {[
      [t('obs.input'), rates.input], [t('obs.cacheRead'), rates.cacheRead],
      ...(rates.cacheWrite !== undefined ? [[t('obs.cacheWrite'), rates.cacheWrite]] : []),
      ...(rates.cacheWriteMax !== undefined ? [[t('obs.costCacheHour'), rates.cacheWriteMax]] : []),
      [t('obs.output'), rates.output],
    ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{currency} {formatNumber(Number(value), { maximumFractionDigits: 5 })}</dd></div>)}
  </dl>;
}

function CostDetails({ api, hours, agent, onClose }: { api: Api; hours: number; agent: string; onClose: () => void }) {
  const titleId = useId();
  const [report, setReport] = useState<CostReport | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let alive = true;
    setReport(null); setError(false);
    api.usageCosts(hours, agent).then(data => { if (alive) setReport(data); }, () => { if (alive) setError(true); });
    return () => { alive = false; };
  }, [api, hours, agent, retry]);
  return createPortal(<DialogSurface labelledBy={titleId} onClose={onClose} className="obs-cost-dialog">
    <header className="panel-head"><h2 id={titleId} className="panel-title">{t('obs.costDetails')}</h2>
      <button className="icon-btn" onClick={onClose} aria-label={t('common.close')}><IconClose size={18} /></button>
    </header>
    <div className="obs-cost-body">
    <p className="obs-note">{t('obs.costBasis')}</p>
    {error ? <div role="alert"><p>{t('obs.loadFailed')}</p><button className="text-btn" onClick={event => {
      // The retry button unmounts while loading; keep keyboard focus inside the nested dialog.
      event.currentTarget.closest<HTMLElement>('[role="dialog"]')?.focus();
      setRetry(n => n + 1);
    }}>{t('common.retry')}</button></div>
      : !report ? <p className="panel-empty">{t('obs.loading')}</p> : <>
        <div className="obs-cost-breakdown">
          <div><span>{t('obs.reportedCost')}</span><strong>{totals(report.reported)}</strong></div>
          <div><span>{t('obs.apiEstimate')}</span><strong>{totals(report.estimated)}</strong></div>
        </div>
        <p className="obs-note">{t('obs.priceCheckedAt', report.priceCheckedAt)}{report.stale ? ` · ${t('obs.priceStale')}` : ''}</p>
        <p className="obs-note">{t('obs.costRangeHint')}</p>
        {report.unmeasuredTurns > 0 && <p>{t('obs.costUnmeasured', report.unmeasuredTurns)}</p>}
        {report.groups.length === 0 && <p className="panel-empty">{t('obs.costNoEstimates')}</p>}
        {report.groups.map((group, i) => <article className="obs-price-group" key={`${group.model}-${i}`}>
          <div className="obs-price-heading"><strong>{group.alias ? `${group.alias} → ${group.model}` : group.model || t('obs.modelUnknown')}</strong><span>{t('obs.samples', group.samples)}</span></div>
          {group.reason ? <p>{t(`obs.costReason.${group.reason}`)}</p> : <>
            <p className="obs-price-amount">{range(group.currency!, group)}</p>
            {group.price && group.price.model !== group.model && <p className="obs-note">{t('obs.costBorrowedPrice', group.price.model)}</p>}
            {group.assumptions.map(assumption => <p className="obs-note" key={assumption}>{t(`obs.costAssumption.${assumption}`)}</p>)}
            {group.price && <>
              <p className="obs-note">{t('obs.costPerMillion')} · <a href={group.price.source} target="_blank" rel="noreferrer">{t('obs.officialPricing')}</a></p>
              <Rates rates={group.price.rates} currency={group.price.currency} />
              {group.price.peakRates && <><p className="obs-note">{t('obs.costPeakRates')}</p><Rates rates={group.price.peakRates} currency={group.price.currency} /></>}
              {group.price.longContext && <><p className="obs-note">{t('obs.costLongContext', group.price.longContext.above)}</p><Rates rates={group.price.longContext.rates} currency={group.price.currency} /></>}
            </>}
          </>}
        </article>)}
      </>}
    </div>
  </DialogSurface>, document.body);
}

export function UsageCost({ summary, api, hours, agent }: { summary: CostSummary; api: Api; hours: number; agent: string }) {
  const [open, setOpen] = useState(false);
  const hasEstimate = summary.estimatedSamples > 0;
  const title = hasEstimate ? summary.reportedSamples > 0 ? t('obs.combinedCost') : t('obs.apiEstimate') : t('obs.reportedCost');
  return <div className="obs-metric">
    <span>{title}</span>
    <strong className="obs-cost">{Object.keys(summary.totals).length
      ? Object.entries(summary.totals).map(([currency, value]) => <span key={currency}>{range(currency, value)}</span>)
      : t('common.unknown')}</strong>
    <div className="obs-cost-notes">
      <small>{summary.unpricedSamples ? t('obs.costUnpriced', summary.unpricedSamples) : hasEstimate ? t('obs.costListPrice') : t('obs.costOnly')}</small>
      {summary.familySamples > 0 && <small>{t('obs.costFamilySamples', summary.familySamples)}</small>}
      {summary.unmeasuredTurns > 0 && <small>{t('obs.costUnmeasured', summary.unmeasuredTurns)}</small>}
      {hasEstimate && summary.stale && <small>{t('obs.priceStale')}</small>}
    </div>
    <button className="text-btn obs-cost-link" onClick={() => setOpen(true)}>{t('obs.costDetails')}</button>
    {open && <CostDetails api={api} hours={hours} agent={agent} onClose={() => setOpen(false)} />}
  </div>;
}
