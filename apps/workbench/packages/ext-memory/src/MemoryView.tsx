import { useEffect, useState } from 'react';
import type { MemoryDocument, MemoryIndex, MemoryKind, MemorySource } from '@calli/extension-api';
import type { ExtensionViewProps } from '@calli/extension-api/web';

const KINDS: MemoryKind[] = ['summary', 'durable', 'evidence', 'raw', 'skill', 'other'];

export function MemoryView({ host }: ExtensionViewProps) {
  const { memory, narrow } = host;
  const words = memory.messages();
  const { Icon, Popover, PopoverOption, RelativeTime, EmptyState, Markdown } = host.ui;
  const [sources, setSources] = useState<MemorySource[] | null>(null);
  const [sourceId, setSourceId] = useState('');
  const [index, setIndex] = useState<{ source: string; data: MemoryIndex } | null>(null);
  const [selected, setSelected] = useState('');
  const [document, setDocument] = useState<{ source: string; data: MemoryDocument } | null>(null);
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState<MemoryKind | 'all'>('all');
  const [original, setOriginal] = useState(false);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [sourceFailed, setSourceFailed] = useState(false);
  const [readFailed, setReadFailed] = useState(false);
  const [connection, setConnection] = useState(host.connection.state());

  useEffect(() => memory.subscribe(() => setRevision(n => n + 1)), [memory]);
  useEffect(() => host.connection.subscribe(setConnection), [host.connection]);

  useEffect(() => {
    let stale = false;
    memory.sources().then(next => {
      if (stale) return;
      setSources(next);
      setSourceFailed(false);
      setSourceId(current => next.some(s => s.id === current) ? current : next[0]?.id ?? '');
    }, () => { if (!stale) setSourceFailed(true); });
    return () => { stale = true; };
  }, [memory, revision]);

  useEffect(() => {
    if (!sourceId) return;
    let stale = false;
    setBusy(true);
    memory.list(sourceId).then(data => {
      if (stale) return;
      setIndex({ source: sourceId, data });
      setFailed(false);
      setSelected(current => data.files.some(f => f.id === current) ? current : narrow ? '' : data.files[0]?.id ?? '');
    }, () => { if (!stale) setFailed(true); }).finally(() => { if (!stale) setBusy(false); });
    return () => { stale = true; };
  }, [memory, sourceId, revision, narrow]);

  const current = index?.source === sourceId ? index.data : null;
  const file = current?.files.find(f => f.id === selected);
  useEffect(() => {
    setReadFailed(false);
    if (!sourceId || !selected) return;
    let stale = false;
    memory.read(sourceId, selected).then(data => {
      if (!stale) setDocument({ source: sourceId, data });
    }, () => { if (!stale) setReadFailed(true); });
    return () => { stale = true; };
  }, [memory, sourceId, selected, file?.modifiedAt, revision]);

  const source = sources?.find(s => s.id === sourceId);
  const name = (s: MemorySource) => s.agents.map(a => a.label).join(' / ');
  const terms = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  const files = current?.files.filter(f => (kind === 'all' || f.kind === kind)
    && terms.every(term => `${f.title} ${f.path} ${f.excerpt}`.toLocaleLowerCase().includes(term))) ?? [];
  const body = document?.source === sourceId && document.data.id === selected ? document.data : null;
  const stale = failed || sourceFailed || (connection !== 'live' && host.connection.observedAt() !== null);
  const mobileDetail = narrow && Boolean(file);
  const empty = sourceFailed && !sources ? words.unreadable
    : sources?.length === 0 ? words.noSources
    : !current ? failed ? words.unreadable : words.loading
    : current.state !== 'available' ? words[current.state]
    : !files.length ? words.noResults : null;

  const changeSource = (id: string) => {
    setSourceId(id); setSelected(''); setDocument(null); setQuery(''); setKind('all'); setFailed(false);
  };

  return <div className={`memory-page${mobileDetail ? ' memory-detail-open' : ''}`}>
    <header className="memory-head">
      <div className="memory-heading">
        <span className="memory-eyebrow"><Icon name="sparkle" size={14} />{words.readOnly}</span>
        <h1>{words.title}</h1>
        <p>{words.subtitle}</p>
      </div>
      <button type="button" className="memory-refresh" disabled={busy} onClick={() => setRevision(n => n + 1)}>
        <Icon name="clock" size={14} />{busy ? words.loading : words.refresh}
      </button>
    </header>
    <div className="memory-source-bar">
      {source && <Popover label={words.source} triggerClassName="memory-source-picker"
        trigger={<><Icon name="server" size={15} /><span>{name(source)}</span><Icon name="chevron" size={12} /></>}
      >{close => <>{sources?.map(s => <PopoverOption key={s.id} selected={s.id === sourceId} onSelect={() => { changeSource(s.id); close(); }}>{name(s)}</PopoverOption>)}</>}</Popover>}
      {source && source.agents.length > 1 && <span className="memory-shared">{words.shared}</span>}
      <span className="memory-scope">{words.scope}</span>
    </div>
    {stale && <p className="memory-warning" role="status">{words.stale}</p>}
    {current?.partial && <p className="memory-warning" role="status">{words.partial}</p>}
    <div className="memory-layout">
      <section className="memory-library" aria-label={words.title}>
        <div className="memory-search"><input type="search" aria-label={words.search} placeholder={words.search} value={query} onChange={e => setQuery(e.target.value)} /></div>
        <div className="memory-filters" aria-label={words.title}>
          {(['all', ...KINDS.filter(k => current?.files.some(f => f.kind === k))] as const).map(k =>
            <button type="button" key={k} aria-pressed={kind === k} onClick={() => setKind(k)}>{words[k]}</button>)}
        </div>
        <div className="memory-list-label"><span>{words.documents(files.length)}</span>{current && <span title={current.checkedAt}>{words.checked} · <RelativeTime iso={current.checkedAt} /></span>}</div>
        <div className="memory-list">
          {empty ? <EmptyState text={empty} /> : files.map(f => <button type="button" className="memory-card" key={f.id} aria-pressed={selected === f.id} onClick={() => { setSelected(f.id); setOriginal(false); }}>
            <span className="memory-card-top"><span className={`memory-kind memory-kind-${f.kind}`}>{words[f.kind]}</span><RelativeTime iso={f.modifiedAt} /></span>
            <strong>{f.title}</strong>
            <span className="memory-excerpt">{f.excerpt || f.path}</span>
            <span className="memory-file-path">{f.path}</span>
          </button>)}
        </div>
      </section>
      <section className="memory-detail" aria-label={file?.title ?? words.preview}>
        {file ? <>
          <div className="memory-detail-toolbar">
            {narrow && <button type="button" className="memory-back" onClick={() => setSelected('')}><Icon name="chevron" size={14} />{words.back}</button>}
            <span className={`memory-kind memory-kind-${file.kind}`}>{words[file.kind]}</span>
            <div className="memory-display-switch">
              <button type="button" aria-pressed={!original} onClick={() => setOriginal(false)}>{words.preview}</button>
              <button type="button" aria-pressed={original} onClick={() => setOriginal(true)}>{words.original}</button>
            </div>
          </div>
          <div className="memory-document">
            <h2>{file.title}</h2>
            <p className="memory-document-path">{file.path}</p>
            <p className="memory-modified">{words.modified} · <RelativeTime iso={body?.modifiedAt ?? file.modifiedAt} /></p>
            {readFailed ? <p role="alert">{words.readFailed}</p> : !body ? <p role="status">{words.loading}</p> : <>
              {body.truncated && <p className="memory-warning">{words.truncated}</p>}
              {original ? <pre className="memory-original">{body.text}</pre> : <Markdown text={body.text.replace(/^#{1,6}[ \t]+(.+)\r?\n(?:\r?\n)*/, (heading, title: string) => title.trim() === file.title ? '' : heading)} />}
            </>}
          </div>
        </> : <div className="memory-detail-empty"><Icon name="sparkle" size={30} /><p>{words.choose}</p></div>}
      </section>
    </div>
  </div>;
}
