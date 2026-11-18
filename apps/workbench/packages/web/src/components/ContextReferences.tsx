import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { AgentMention, Api, ProjectDirectoryInfo, Thread } from '../api';
import type { ContextReference } from '../context-references';
import { t } from '../i18n';
import { Dialog } from './Dialog';
import { IconAgents, IconChevron, IconClose, IconFolder, IconPaperclip, IconQuote } from './icons';

export function ReferenceChips({ references, onRemove }: { references: ContextReference[]; onRemove?: (id: string) => void }) {
  const [detail, setDetail] = useState<ContextReference | null>(null);
  if (!references.length) return null;
  return <><div className="context-references" aria-label={t('references.title')}>
    {references.map(reference => <div className="context-reference" key={reference.id}>
      <button type="button" onClick={() => setDetail(reference)} title={reference.path}>
        {reference.kind === 'thread' ? <IconQuote size={14} /> : reference.kind === 'message' ? <IconPaperclip size={14} /> : <IconFolder size={14} />}<span>{reference.kind === 'message' ? t('references.message', reference.title) : reference.title}{reference.kind === 'directory' ? '/' : ''}</span>
      </button>
      {onRemove && <button type="button" aria-label={t('references.remove', reference.title)} onClick={() => onRemove(reference.id)}><IconClose size={13} /></button>}
    </div>)}
  </div>{detail && createPortal(<Dialog title={detail.title} className="reference-detail" confirmLabel={t('common.close')} hideCancel onConfirm={() => setDetail(null)} onCancel={() => setDetail(null)}>
    <p className="reference-path">{detail.path}</p>
    <p>{t(detail.kind === 'thread' ? 'references.threadHint' : detail.kind === 'message' ? 'references.messageHint' : 'references.directoryHint')}</p>
    {detail.kind !== 'directory' && <p className="muted">{t('references.snapshot', new Date(detail.at).toLocaleString())}</p>}
  </Dialog>, document.body)}</>;
}

export interface ReferenceSource { api: Api; cwd: string; threadId?: string }

/** Mounted only while the picker is open; never preload transcripts or recursive trees. */
export function ReferencePicker({ source, initialQuery, agents, onAgent, onPick }: {
  source: ReferenceSource; initialQuery: string; agents: AgentMention[];
  onAgent: (agent: AgentMention) => void; onPick: (reference: ContextReference) => void;
}) {
  const [query, setQuery] = useState(initialQuery);
  // One kind of reference at a time: sessions first, folders when an address is typed, agents
  // one tap away. The three lists never share the screen, which made a long scroll on a phone.
  const [tab, setTab] = useState<'threads' | 'dirs' | 'agents'>(initialQuery.includes('/') ? 'dirs' : 'threads');
  const [folder, setFolder] = useState(source.cwd);
  const [threads, setThreads] = useState<Thread[] | null>(null);
  const [listing, setListing] = useState<ProjectDirectoryInfo | null>(null);
  const [threadError, setThreadError] = useState('');
  const [folderError, setFolderError] = useState('');
  const [pickError, setPickError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [retry, setRetry] = useState(0);
  const mounted = useRef(true);
  const search = useRef<HTMLInputElement>(null);
  useEffect(() => { mounted.current = true; search.current?.focus(); return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    let live = true;
    setThreadError('');
    void source.api.threads().then(value => { if (live) setThreads(value); }, () => { if (live) setThreadError(t('references.loadFailed')); });
    return () => { live = false; };
  }, [source.api, retry]);
  const slash = query.lastIndexOf('/');
  const typed = slash >= 0 ? query.slice(0, slash + 1) : '';
  const path = typed ? typed.startsWith('/') || typed.startsWith('~/') ? typed : `${folder}/${typed}` : folder;
  const filter = (slash >= 0 ? query.slice(slash + 1) : query).toLowerCase();
  useEffect(() => {
    let live = true;
    setLoading(true); setFolderError(''); setListing(null);
    const timer = setTimeout(() => {
      void source.api.projectDirectories(path || undefined).then(value => { if (live) setListing(value); }, () => { if (live) setFolderError(t('references.loadFailed')); })
        .finally(() => { if (live) setLoading(false); });
    }, 120);
    return () => { live = false; clearTimeout(timer); };
  }, [source.api, path, retry]);
  const pick = async (input: { kind: 'thread'; sourceId: string } | { kind: 'directory'; path: string }) => {
    if (busy) return;
    setBusy(true); setPickError('');
    try { const reference = await source.api.createReference(input); if (mounted.current) onPick(reference); }
    catch (error) { if (mounted.current) setPickError(error instanceof Error ? error.message : t('references.loadFailed')); }
    finally { if (mounted.current) setBusy(false); }
  };
  const go = (next: string) => { setFolder(next); setQuery(''); };
  const matches = (threads ?? []).filter(thread => thread.id !== source.threadId && `${thread.title} ${thread.cwd}`.toLowerCase().includes(query.toLowerCase())).slice(0, 8);
  const options = agents.filter(agent => `${agent.label} ${agent.id}`.toLowerCase().includes(query.toLowerCase())).slice(0, 8);
  const tabs = [
    { id: 'threads' as const, label: t('references.tab.threads'), count: threads === null ? null : matches.length },
    { id: 'dirs' as const, label: t('references.tab.directories'), count: listing ? listing.directories.filter(dir => dir.name.toLowerCase().includes(filter)).length : null },
    { id: 'agents' as const, label: t('references.tab.agents'), count: options.length },
  ];
  return <div className="reference-picker" onKeyDown={event => {
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
    if (event.target === search.current && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
      event.preventDefault(); const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
      const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
      buttons[(index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]?.focus();
    }
    if (event.key === 'Enter' && event.target === search.current) { event.preventDefault(); event.currentTarget.querySelector<HTMLButtonElement>('[data-reference-pick]')?.click(); }
  }}>
    <input ref={search} className="reference-search" aria-label={t('references.search')} placeholder={t('references.search')} value={query}
      onChange={event => { const value = event.target.value; setQuery(value); if (value.includes('/')) setTab('dirs'); }} />
    <div className="reference-tabs" role="tablist">
      {tabs.map(item => <button key={item.id} type="button" role="tab" aria-selected={tab === item.id}
        className={tab === item.id ? 'on' : undefined} onClick={() => setTab(item.id)}>
        <span>{item.label}</span>
        {item.count !== null && <span className="reference-tab-count">{item.count}</span>}
      </button>)}
    </div>
    <div className="reference-results" aria-busy={busy} role="tabpanel">
      {tab === 'threads' && <>
        {threadError ? <p role="alert">{threadError} <button onClick={() => setRetry(value => value + 1)}>{t('references.retry')}</button></p>
          : threads === null ? <p className="reference-state">{t('references.loading')}</p>
          : !matches.length ? <p className="reference-state">{t('references.empty')}</p>
          : matches.map(thread => <button data-reference-pick className="reference-option" key={thread.id} disabled={busy} onClick={() => void pick({ kind: 'thread', sourceId: thread.id })}>
            <IconQuote size={16} /><span><strong>{thread.title}</strong><small>{thread.cwd}</small></span>
          </button>)}</>}
      {tab === 'dirs' && <>
        {folderError ? <p role="alert">{folderError} <button onClick={() => setRetry(value => value + 1)}>{t('references.retry')}</button></p>
          : loading || !listing ? <p className="reference-state">{t('references.loading')}</p> : <>
            <div className="reference-location"><span title={listing.path}>{listing.path}</span>{listing.parent && <button disabled={busy} onClick={() => go(listing.parent!)}>{t('references.parent')}</button>}</div>
            <button className="reference-option" data-reference-pick disabled={busy} onClick={() => void pick({ kind: 'directory', path: listing.path })}><IconFolder size={16} /><span>{t('references.thisDirectory')}</span></button>
            {listing.directories.filter(dir => dir.name.toLowerCase().includes(filter)).slice(0, 30).map(dir => <div className="reference-directory" key={dir.path}>
              <button className="reference-option" data-reference-pick disabled={busy} onClick={() => void pick({ kind: 'directory', path: dir.path })}><IconFolder size={16} /><span>{dir.name}/</span></button>
              <button className="icon-btn" disabled={busy} aria-label={t('references.browse', dir.name)} onClick={() => go(dir.path)}><IconChevron size={16} /></button>
            </div>)}
            {!listing.directories.some(dir => dir.name.toLowerCase().includes(filter)) && <p className="reference-state">{t('references.empty')}</p>}
          </>}
      </>}
      {tab === 'agents' && (options.length
        ? options.map(agent => <button className="reference-option" key={`${agent.kind}:${agent.id}`} disabled={busy} onClick={() => onAgent(agent)}><IconAgents size={16} /><span>{agent.label}</span></button>)
        : <p className="reference-state">{t('references.empty')}</p>)}
    </div>
    <div className="reference-status" role="status">{pickError || (busy ? t('references.adding') : t('references.localOnly'))}</div>
  </div>;
}
