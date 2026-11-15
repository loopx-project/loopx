import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, type Api, type GitCommit, type GitCommitFiles, type GitDiff, type GitFile, type GitLog, type GitStatus } from '../api';
import { localeTag, t } from '../i18n';
import { folderName } from '../paths';
import { IconArrowDown, IconArrowLeft, IconChevronRight, IconClose, IconRefresh } from './icons';

function errorText(error: unknown): string {
  const code = error instanceof ApiError ? error.details.gitCode : '';
  switch (code) {
    case 'dirty': return t('git.errorDirty');
    case 'diverged': return t('git.errorDiverged');
    case 'agent_busy': return t('git.errorAgent');
    case 'operation_in_progress': return t('git.errorOperation');
    case 'detached': return t('git.errorDetached');
    case 'no_upstream': return t('git.errorUpstream');
    case 'no_remote': return t('git.errorRemote');
    case 'busy': return t('git.errorBusy');
    case 'timeout': return t('git.errorTimeout');
    case 'too_large': return t('git.errorLarge');
    case 'file_changed': case 'invalid_path': case 'unsupported_file': return t('git.errorFile');
    case 'command_failed': return t('git.errorCommand');
    default: return error instanceof ApiError && error.status === 404 ? error.message : t('git.loadFailed');
  }
}

function pullBlock(status: GitStatus, running: boolean): string | null {
  if (running || status.agentBusy) return t('git.errorAgent');
  if (status.operation) return t('git.errorOperation');
  if (!status.branch) return t('git.errorDetached');
  if (!status.upstream || status.ahead === null || status.behind === null) return t('git.errorUpstream');
  if (status.files.length) return t('git.errorDirty');
  if (status.ahead > 0 && status.behind > 0) return t('git.errorDiverged');
  return null;
}

type Detail = { kind: 'commit'; commit: GitCommit } | { kind: 'file'; file: GitFile; staged: boolean; commit?: GitCommit };

/** A checkout's Git alongside the conversation; no reads happen until the panel is opened. */
export function GitPanel({ api, threadId, phone, running, onClose }: {
  api: Api; threadId: string; phone: boolean; running: boolean; onClose: () => void;
}) {
  const [status, setStatus] = useState<GitStatus | null>(null);
  const [busy, setBusy] = useState<'refresh' | 'fetch' | 'pull' | null>('refresh');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [tab, setTab] = useState<'changes' | 'history'>('changes');
  const [detail, setDetail] = useState<Detail | null>(null);
  const [revision, setRevision] = useState(0);
  const live = useRef(true);
  const request = useRef<AbortController | null>(null);
  const pending = useRef(false);
  const closeRef = useRef(onClose); closeRef.current = onClose;
  const pushed = useRef(false);
  useEffect(() => {
    if (!phone) return;
    window.history.pushState({ ...window.history.state, calliGit: true }, '');
    pushed.current = true;
    const pop = () => { if (pushed.current) { pushed.current = false; closeRef.current(); } };
    window.addEventListener('popstate', pop);
    return () => {
      window.removeEventListener('popstate', pop);
      if (pushed.current) { pushed.current = false; window.history.back(); }
    };
  }, [phone]);
  const close = useCallback(() => pushed.current ? window.history.back() : closeRef.current(), []);
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); close(); } };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [close]);

  const refresh = useCallback(async () => {
    if (pending.current) return;
    pending.current = true;
    const ac = new AbortController(); request.current?.abort(); request.current = ac;
    setBusy('refresh');
    try {
      const value = await api.gitStatus(threadId, ac.signal);
      if (!ac.signal.aborted) { setStatus(value); setError(''); setMessage(t('git.refreshed')); setRevision(n => n + 1); }
    } catch (e) { if (!ac.signal.aborted) { setError(errorText(e)); setMessage(t('git.stale')); } }
    finally { if (!ac.signal.aborted) { pending.current = false; setBusy(null); } }
  }, [api, threadId]);
  useEffect(() => {
    live.current = true; pending.current = false; void refresh();
    return () => { live.current = false; request.current?.abort(); };
  }, [refresh]);

  const operate = async (operation: 'fetch' | 'pull') => {
    if (pending.current) return;
    pending.current = true; setBusy(operation);
    try {
      const result = await api.gitOperate(threadId, operation);
      if (!live.current) return;
      if (result.status) setStatus(result.status);
      setError(result.statusError ? t('git.readAfterWrite') : '');
      setMessage(result.statusError ? t('git.readAfterWrite') : operation === 'fetch' ? t('git.fetched') : result.changed ? t('git.pulled') : t('git.current'));
      setRevision(n => n + 1);
    } catch (e) {
      if (!live.current) return;
      setError(errorText(e)); setMessage(errorText(e));
      // Fetch can update refs even when a later remote fails; pull can refuse after fetching.
      try { const value = await api.gitStatus(threadId); if (live.current) setStatus(value); } catch { /* Keep the last known snapshot. */ }
    } finally { if (live.current) { pending.current = false; setBusy(null); } }
  };
  const block = status?.isRepo ? pullBlock(status, running) : null;
  const statusLine = busy ? t(busy === 'fetch' ? 'git.fetching' : busy === 'pull' ? 'git.pulling' : 'git.refreshing') : message || t('git.localOnly');
  const back = () => setDetail(detail?.kind === 'file' && detail.commit ? { kind: 'commit', commit: detail.commit } : null);
  const openFile = (file: GitFile, staged: boolean, commit?: GitCommit) => setDetail({ kind: 'file', file, staged, commit });
  return <aside className={`git-panel${phone ? ' phone' : ''}`} aria-label={t('git.label')}>
    <header className="git-head">
      {detail && <button className="icon-btn" aria-label={t('common.back')} onClick={back}><IconArrowLeft /></button>}
      <strong>{t('git.label')}</strong>
      <button className={`icon-btn${busy === 'refresh' ? ' git-spinning' : ''}`} aria-label={t('git.refresh')} title={t('git.refresh')} disabled={!!busy} onClick={() => void refresh()}><IconRefresh /></button>
      <button className="icon-btn" aria-label={t('common.close')} onClick={close}><IconClose /></button>
    </header>
    {error && <div className="git-error" role="alert">{error}<button className="text-btn" disabled={!!busy} onClick={() => void refresh()}>{t('common.retry')}</button></div>}
    <div className="git-body" hidden={!!detail}>
      {!status ? busy ? <div className="git-skeleton" aria-label={t('common.loading')}><i /><i /><i /><i /><i /></div> : <div className="git-empty">{t('git.loadFailed')}</div>
        : !status.isRepo ? <div className="git-empty">{t('git.notRepo')}</div>
        : <>
          <div className="git-repo" title={status.root ?? ''}>{folderName(status.root ?? '')}</div>
          <div className="git-branch">{status.branch ?? t('git.detached')}</div>
          <div className="git-tracking"><span title={status.upstream ?? ''}>{status.upstream ? t('git.tracking', status.upstream) : t('git.noUpstream')}</span><span>↑ {status.ahead ?? t('git.unknown')}　↓ {status.behind ?? t('git.unknown')}</span></div>
          <div className="git-actions"><button className="secondary-btn" disabled={!!busy} onClick={() => void operate('fetch')}><IconRefresh size={15} />{t('git.fetch')}</button><button className="primary-btn" disabled={!!busy || !!block} onClick={() => void operate('pull')}><IconArrowDown size={15} />{t('git.pull')}</button></div>
          <p className="git-hint">{t('git.syncHint')}</p>
          {block && <p className="git-block" role="status">{block}</p>}
          <div className="git-tabs" role="tablist" aria-label={t('git.label')}><button role="tab" aria-selected={tab === 'changes'} onClick={() => setTab('changes')}>{t('git.changes')} {status.files.length || ''}</button><button role="tab" aria-selected={tab === 'history'} onClick={() => setTab('history')}>{t('git.history')}</button></div>
          {tab === 'changes' ? <div role="tabpanel">
            {!status.files.length && <div className="git-empty">{t('git.clean')}</div>}
            <Files label={t('git.unstaged')} files={status.files.filter(f => f.worktree !== ' ' && f.worktree !== '?')} onOpen={f => openFile(f, false)} />
            <Files label={t('git.staged')} files={status.files.filter(f => f.index !== ' ' && f.index !== '?')} onOpen={f => openFile(f, true)} />
            <Files label={t('git.untracked')} files={status.files.filter(f => f.index === '?')} onOpen={f => openFile(f, false)} />
          </div> : <History key={status.head ?? 'empty'} api={api} threadId={threadId} head={status.head} onOpen={commit => setDetail({ kind: 'commit', commit })} />}
        </>}
    </div>
    {detail && <GitDetail key={JSON.stringify(detail)} api={api} threadId={threadId} detail={detail} revision={revision} onFile={openFile} />}
    <footer className="git-foot" role="status" title={statusLine}>
      <span>{statusLine}</span>
      {status?.isRepo && <span>{status.fetchedAt ? t('git.fetchedAt', new Date(status.fetchedAt).toLocaleTimeString(localeTag(), { hour: '2-digit', minute: '2-digit' })) : t('git.localOnly')}</span>}
    </footer>
  </aside>;
}

function Files({ label, files, onOpen }: { label: string; files: GitFile[]; onOpen: (file: GitFile) => void }) {
  if (!files.length) return null;
  return <section className="git-files"><div className="git-section"><span>{label}</span><span>{t('git.fileCount', files.length)}</span></div>{files.map(file => {
    const slash = file.path.lastIndexOf('/');
    return <button key={file.path} className="git-file" onClick={() => onOpen(file)} title={file.originalPath ? `${file.originalPath} → ${file.path}` : file.path}>
      <span className="git-status-code">{file.index.trim() || file.worktree}</span><span className="git-file-name"><strong>{file.path.slice(slash + 1)}</strong><small>{slash >= 0 ? file.path.slice(0, slash) : ''}</small></span><IconChevronRight size={14} />
    </button>;
  })}</section>;
}

function History({ api, threadId, head, onOpen }: { api: Api; threadId: string; head: string | null; onOpen: (commit: GitCommit) => void }) {
  const [value, setValue] = useState<GitLog | null>(null), [error, setError] = useState(''), [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const ac = useRef<AbortController | null>(null);
  useEffect(() => {
    const control = new AbortController(); ac.current = control; setLoading(true);
    api.gitLog(threadId, head, 0, control.signal).then(v => { if (!control.signal.aborted) { setValue(v); setError(''); } }).catch(e => { if (!control.signal.aborted) setError(errorText(e)); }).finally(() => { if (!control.signal.aborted) setLoading(false); });
    return () => control.abort();
  }, [api, threadId, head, attempt]);
  const older = async () => {
    if (!value || loading) return;
    setLoading(true);
    try { const next = await api.gitLog(threadId, value.head, value.commits.length, ac.current?.signal); if (!ac.current?.signal.aborted) { setValue({ ...next, commits: [...value.commits, ...next.commits] }); setError(''); } }
    catch (e) { if (!ac.current?.signal.aborted) setError(errorText(e)); }
    finally { if (!ac.current?.signal.aborted) setLoading(false); }
  };
  return <div role="tabpanel">
    {!value && loading && <div className="git-empty">{t('common.loading')}</div>}
    {value?.commits.map(c => <button key={c.hash} className="git-commit" onClick={() => onOpen(c)}><strong>{c.subject}</strong><small>{c.hash.slice(0, 8)} · {c.author} · {new Date(c.date).toLocaleDateString(localeTag())}</small></button>)}
    {value && !value.commits.length && <div className="git-empty">{t('git.noCommits')}</div>}
    {error && <div className="git-error" role="alert">{error}<button className="text-btn" disabled={loading} onClick={() => value ? void older() : setAttempt(n => n + 1)}>{t('common.retry')}</button></div>}
    {value?.more && <button className="git-load-more text-btn" disabled={loading} onClick={() => void older()}>{loading ? t('common.loading') : t('git.older')}</button>}
  </div>;
}

function GitDetail({ api, threadId, detail, revision, onFile }: { api: Api; threadId: string; detail: Detail; revision: number; onFile: (file: GitFile, staged: boolean, commit?: GitCommit) => void }) {
  const [value, setValue] = useState<GitCommitFiles | GitDiff | null>(null), [error, setError] = useState(''), [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const ac = new AbortController(); setLoading(true);
    const read = detail.kind === 'commit' ? api.gitCommit(threadId, detail.commit.hash, ac.signal) : api.gitDiff(threadId, detail.file.path, detail.staged, detail.commit?.hash, ac.signal);
    read.then(v => { if (!ac.signal.aborted) { setValue(v); setError(''); } }).catch(e => { if (!ac.signal.aborted) setError(errorText(e)); }).finally(() => { if (!ac.signal.aborted) setLoading(false); });
    return () => ac.abort();
  }, [api, threadId, detail, revision, attempt]);
  return <div className="git-body git-detail">
    <h2>{detail.kind === 'commit' ? detail.commit.subject : detail.file.path}</h2>
    <p className="git-hint">{detail.kind === 'commit' ? t('git.firstParent') : detail.commit ? detail.commit.hash.slice(0, 8) : detail.staged ? t('git.staged') : detail.file.index === '?' ? t('git.untracked') : t('git.unstaged')}</p>
    <div className="git-detail-progress" role="status">{loading ? t('common.loading') : ''}</div>
    {error && <div className="git-error" role="alert">{error}<button className="text-btn" disabled={loading} onClick={() => setAttempt(n => n + 1)}>{t('common.retry')}</button></div>}
    {value && 'files' in value && detail.kind === 'commit' && (value.files.length ? <Files label={t('git.commitFiles')} files={value.files} onOpen={f => onFile(f, true, detail.commit)} /> : <div className="git-empty">{t('git.emptyDiff')}</div>)}
    {value && 'text' in value && <>
      {value.truncated && <p className="git-block">{t('git.truncated')}</p>}
      {value.binary ? <div className="git-empty">{t('git.binary')}</div> : value.text ? <pre className="git-diff">{value.text.split('\n').map((line, i) => <span key={i} className={value.untracked || line.startsWith('+') && !line.startsWith('+++') ? 'git-added' : line.startsWith('-') && !line.startsWith('---') ? 'git-removed' : line.startsWith('@@') ? 'git-hunk' : ''}>{line || ' '}{'\n'}</span>)}</pre> : <div className="git-empty">{t('git.emptyDiff')}</div>}
    </>}
  </div>;
}
