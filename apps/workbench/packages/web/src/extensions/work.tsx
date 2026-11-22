import { useEffect, useState } from 'react';
import type { ExtensionState, WorkProjection } from '@calli/extension-api';
import type { Api } from '../api';
import { locale, t, formatDateTime } from '../i18n';
import { PanelDialog } from '../components/Dialog';
import { IconChevron, IconTarget } from '../components/icons';

/** The server owns snapshots; both core and views read the same projection. */
export function useWorkSources(api: Api, extensions: ExtensionState[] | null, active: boolean, consumer?: string, threadId?: string) {
  const [items, setItems] = useState<WorkProjection[]>([]);
  useEffect(() => {
    let alive = true, busy = false;
    setItems([]);
    if (!active || api.demo || !extensions?.some(s => s.source === 'external' && s.installed)) return;
    if (consumer && !extensions.some(s => s.manifest.id === consumer && s.enabled && s.granted.includes('worksource.read'))) return;
    const read = async () => {
      if (busy || document.visibilityState === 'hidden') return;
      busy = true;
      try { const next = await api.workSources({ consumer, threadId, refresh: true }); if (alive) setItems(next); }
      catch { if (alive) setItems(old => old.map(p => ({ ...p, freshness: p.snapshot ? 'stale' : 'unknown' }))); }
      finally { busy = false; }
    };
    void read();
    const timer = setInterval(() => void read(), 30000);
    document.addEventListener('visibilitychange', read);
    document.addEventListener('work-sources:refresh', read);
    return () => { alive = false; clearInterval(timer); document.removeEventListener('visibilitychange', read); document.removeEventListener('work-sources:refresh', read); };
  }, [api, extensions, active, consumer, threadId]);
  return items;
}

export function WorkSummary({ items }: { items: WorkProjection[] }) {
  const [selected, setSelected] = useState<string | null>(null);
  const current = items.find(p => `${p.sourceId}/${p.binding.id}` === selected);
  if (!items.length) return null;
  return <div className="work-summary" onClick={e => e.stopPropagation()}>
    {items.map(p => <button key={`${p.sourceId}/${p.binding.id}`} className="work-summary-row" type="button" onClick={() => setSelected(`${p.sourceId}/${p.binding.id}`)}>
      <IconTarget size={14} /><span className="work-source-name">{p.sourceName[locale()]}</span>
      <span className="work-source-title">{p.snapshot?.title ?? p.binding.externalId}</span>
      <span className="work-source-state">{p.freshness === 'fresh' ? (p.snapshot?.statusLabel?.[locale()] ?? p.snapshot?.status) : t(`work.${p.freshness}`)}</span>
      <IconChevron size={12} />
    </button>)}
    {current && <PanelDialog title={current.snapshot?.title ?? current.binding.externalId} className="work-detail" onClose={() => setSelected(null)}>
      <div className="work-detail-body">
        <button type="button" className="ghost" onClick={() => document.dispatchEvent(new Event('work-sources:refresh'))}>{t('common.retry')}</button>
        <div className="work-detail-meta"><span>{current.sourceName[locale()]}</span><span>{t(`work.${current.freshness}`)}</span></div>
        {current.observedAt && <p className="muted">{t('work.observed', formatDateTime(current.observedAt))}</p>}
        {current.snapshot ? <>
          <p className="work-detail-status">{current.snapshot.statusLabel?.[locale()] ?? current.snapshot.status}</p>
          {current.snapshot.summary && <p>{current.snapshot.summary}</p>}
          {!current.snapshot.complete && <p className="work-partial">{t('work.partial')}</p>}
          <ul className="work-items">{current.snapshot.items.map(i => <li key={i.id}><span>{i.text}</span><span className="muted">{i.status}</span></li>)}</ul>
          {!current.snapshot.items.length && <p className="muted">{t('work.noItems')}</p>}
          {current.snapshot.evidence && <p>{current.snapshot.evidence}</p>}
        </> : <p>{t('work.unknown')}</p>}
        {current.error && <p className="muted">{t('work.readFailed')} <code>{current.error}</code></p>}
        {current.binding.url && <a className="work-link" href={current.binding.url} target="_blank" rel="noopener noreferrer">{t('work.openSource')}</a>}
      </div>
    </PanelDialog>}
  </div>;
}
