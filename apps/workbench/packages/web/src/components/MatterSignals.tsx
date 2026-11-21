import { useEffect, useId, useRef, useState } from 'react';
import Markdown, { type Components } from 'react-markdown';
import type { Api, MatterSignal, SignalMaterialRead } from '../api';
import { formatDateTime, t } from '../i18n';
import { signalDestination } from '../lark';
import { boundedSignalMaterial, canRefreshSignalMaterial, SIGNAL_MATERIAL_MAX_CHARS, signalMaterialLine, signalSourceUrl } from '../signals';
import { IconChevron } from './icons';

interface MaterialView {
  open: boolean;
  loading: boolean;
  result?: SignalMaterialRead;
  error?: string;
}

// Material text is not agent output: no HTML, images, interactive tasks, or navigable body links.
const MATERIAL_COMPONENTS: Components = {
  a: ({ children }) => <span>{children}</span>,
  img: ({ alt }) => alt ? <span>{alt}</span> : null,
};
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

function MaterialDetails({ signal, view, partial, onRead }: { signal: MatterSignal; view: MaterialView; partial: boolean; onRead: () => void }) {
  const result = view.result;
  const body = result?.material.state === 'ready' ? boundedSignalMaterial(result.content).text : '';
  const sourceUrl = signalSourceUrl(signal.source?.url);
  return <>
    {(signal.source?.from || sourceUrl) && <div className="desk-material-source">
      {signal.source?.from && <span>{signal.source.from}</span>}
      {sourceUrl && <a href={sourceUrl} target="_blank" rel="noopener noreferrer">{t('desk.material.openSource')}</a>}
    </div>}
    {view.loading ? <p className="desk-signals-note" role="status">{t('desk.material.loading')}</p>
      : view.error || result?.material.state === 'failed' ? <p className="desk-signals-note problem" role="alert">{t('desk.material.failed')}{(view.error || result?.material.error) && <span className="desk-material-error">{view.error || result?.material.error}</span>}</p>
      : !result || result.material.state === 'unread' ? <p className="desk-signals-note">{t('desk.material.unread')}</p>
      : result.material.state === 'not-ready' ? <p className="desk-signals-note">{t('desk.material.notReadyHint')}</p>
      : result.material.state === 'empty' || !body.trim() ? <p className="desk-signals-note">{t(partial ? 'desk.material.partialEmpty' : 'desk.material.empty')}</p>
      : <div className="desk-material-text"><Markdown skipHtml components={MATERIAL_COMPONENTS}>{body}</Markdown></div>}
    {!view.loading && !view.error && result && partial && <p className="desk-material-partial">{t('desk.material.partialHint')}</p>}
    {(view.error || result) && (view.error || canRefreshSignalMaterial(result?.material) || partial) && <button className="desk-material-retry" disabled={view.loading} onClick={onRead}>{t(view.error || result?.material.state === 'failed' ? 'common.retry' : 'desk.material.readAgain')}</button>}
  </>;
}

/** Identity changes discard list and body caches before another matter can be shown. */
export function MatterSignals({ api, matterId }: { api: Api; matterId: string }) {
  return <MatterSignalList key={matterId} api={api} matterId={matterId} />;
}

/** Two levels of disclosure: neither the list nor a meeting body is read until explicitly asked. */
function MatterSignalList({ api, matterId }: { api: Api; matterId: string }) {
  const [open, setOpen] = useState(false);
  const [signals, setSignals] = useState<MatterSignal[] | null>(null);
  const [error, setError] = useState('');
  const [materials, setMaterials] = useState<Map<string, MaterialView>>(() => new Map());
  const bodyId = useId();
  const generation = useRef(0);
  const currentApi = useRef(api);
  const listPending = useRef(false);
  const materialPending = useRef(new Set<string>());
  currentApi.current = api;
  useEffect(() => {
    ++generation.current;
    setOpen(false); setSignals(null); setError(''); setMaterials(new Map());
    listPending.current = false; materialPending.current.clear();
    return () => { ++generation.current; };
  }, [api]);
  const isCurrent = (ticket: number) => ticket === generation.current && currentApi.current === api;
  const updateMaterial = (key: string, patch: Partial<MaterialView>) => {
    setMaterials(current => new Map(current).set(key, { open: true, loading: false, ...current.get(key), ...patch }));
  };
  const load = () => {
    if (listPending.current) return;
    listPending.current = true;
    const ticket = generation.current;
    setError('');
    void api.matterSignals(matterId).then(value => {
      if (isCurrent(ticket)) setSignals(value.signals);
    }).catch(e => { if (isCurrent(ticket)) setError(errorText(e)); }).finally(() => {
      if (isCurrent(ticket)) listPending.current = false;
    });
  };
  const readMaterial = (key: string) => {
    if (materialPending.current.has(key)) return;
    materialPending.current.add(key);
    const ticket = generation.current;
    updateMaterial(key, { loading: true, error: '' });
    void api.readSignalMaterial(key).then(result => {
      if (!isCurrent(ticket)) return;
      if (result.key !== key) throw new Error(t('desk.material.wrongSignal'));
      updateMaterial(key, { result, loading: false });
    }).catch(e => { if (isCurrent(ticket)) updateMaterial(key, { error: errorText(e), loading: false }); }).finally(() => {
      if (isCurrent(ticket)) materialPending.current.delete(key);
    });
  };
  const toggle = () => {
    if (!open && !signals && !error) load();
    setOpen(on => !on);
  };
  const toggleMaterial = (key: string) => {
    const view = materials.get(key);
    const nextOpen = !view?.open;
    updateMaterial(key, { open: nextOpen });
    if (nextOpen && !view?.result && !view?.error) readMaterial(key);
  };
  return <div className="desk-subscriptions desk-signals">
    <button className="desk-subscriptions-toggle" aria-expanded={open} onClick={toggle}>{signals ? t('desk.signalCount', signals.length) : t('desk.signals')}<IconChevron size={13} open={open} /></button>
    {open && (error ? <p className="desk-signals-note problem" role="alert">{error} <button className="ghost" onClick={load}>{t('common.retry')}</button></p>
      : !signals ? <p className="desk-signals-note" role="status">{t('common.loading')}</p>
      : !signals.length ? <p className="desk-signals-note">{t('desk.noSignals')}</p>
      : <ul>{signals.map(signal => {
        const view = materials.get(signal.key);
        const material = view?.result?.material ?? signal.material;
        const partial = material?.partial === true || (view?.result?.content?.length ?? 0) > SIGNAL_MATERIAL_MAX_CHARS;
        const line = view?.loading ? { text: t('desk.material.loading'), warn: false }
          : view?.error ? { text: t('desk.material.failed'), warn: true }
          : signalMaterialLine(material, Boolean(view?.result));
        const summary = signal.summary || signal.meeting?.title || t('desk.signal.noSummary');
        const id = `${bodyId}-${encodeURIComponent(signal.key)}`;
        const read = !view?.result && !view?.error;
        return <li key={signal.key} className="desk-signal" data-signal-key={signal.key}>
          <div className="desk-signal-row">
            <time>{formatDateTime(signal.at, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</time>
            <span className="desk-signal-summary" title={summary}>{summary}</span>
            <span className="desk-signal-where" title={signalDestination(signal.disposition)}>{signalDestination(signal.disposition)}</span>
            {signal.meeting && <>
              <span className={`desk-material-state${line.warn ? ' problem' : ''}`} role="status" title={view?.error || material?.error}>{line.text}{partial && <span className="desk-material-partial-mark">{t('desk.material.partial')}</span>}</span>
              <button className="desk-signal-material-toggle" aria-expanded={view?.open ?? false} aria-controls={id} aria-label={t(read ? 'desk.material.readFor' : 'desk.material.detailsFor', summary)} onClick={() => toggleMaterial(signal.key)}>{t(read ? 'desk.material.read' : 'desk.material.details')}<IconChevron size={12} open={view?.open ?? false} /></button>
            </>}
          </div>
          {signal.meeting && view?.open && <div id={id} className="desk-signal-material"><MaterialDetails signal={signal} view={view} partial={partial} onRead={() => readMaterial(signal.key)} /></div>}
        </li>;
      })}</ul>)}
  </div>;
}
