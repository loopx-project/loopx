import { useCallback, useEffect, useRef, useState } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { t } from '../i18n';
import type { Thread } from '../api';
import { fetchResults, readResult, resultKey, type ResultPage, type ResultRead, type ResultRow } from '../loopx/results';
import { goalConversation } from '../loopx/goal-navigation';

/** The Goal's Files tab: the reports that still pass acceptance, and the workspace folder in the file panel. */
export function GoalFiles({ token, goalId, threads, onBrowse, onOpenConversations }: {
  token: string | undefined;
  goalId: string;
  threads: Thread[];
  /** Open the file panel on this Goal conversation's working folder. */
  onBrowse: (threadId: string) => void;
  onOpenConversations: () => void;
}) {
  const [page, setPage] = useState<ResultPage | null>(null);
  const [open, setOpen] = useState<ResultRead | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const bound = goalConversation(goalId, threads);

  const load = useCallback(async (cursor?: string) => {
    const current = ++generation.current;
    setBusy(true); setError(''); setOpen(null);
    try {
      const next = await fetchResults(token, goalId, cursor);
      if (current === generation.current) setPage(next);
    } catch (cause) {
      // A list that cannot be verified is cleared, so old content never stands for current.
      if (current === generation.current) { setPage(null); setError(`${t('loopx.files.cannotVerify')} ${cause instanceof Error ? cause.message : String(cause)}`); }
    } finally { if (current === generation.current) setBusy(false); }
  }, [token, goalId]);
  useEffect(() => { setPage(null); void load(); return () => { generation.current++; }; }, [load]);

  const select = async (row: ResultRow) => {
    const current = ++generation.current;
    setBusy(true); setError(''); setOpen(null);
    try {
      const read = await readResult(token, goalId, row);
      if (current === generation.current) setOpen(read);
    } catch (cause) {
      if (current === generation.current) setError(`${t('loopx.files.changed')} ${cause instanceof Error ? cause.message : String(cause)}`);
    } finally { if (current === generation.current) setBusy(false); }
  };

  return <div className="goal-files">
    <section className="goal-files-workspace" aria-label={t('loopx.files.workspace')}>
      <h2>{t('loopx.files.workspace')}</h2>
      {bound
        ? <>
          <p className="goal-acceptance-note">{t('loopx.files.workspaceHint')}</p>
          <button type="button" className="primary" onClick={() => onBrowse(bound.id)}>{t('loopx.files.browse')}</button>
        </>
        : <>
          <p className="goal-empty">{t('loopx.files.noThread')}</p>
          <button type="button" className="ghost" onClick={onOpenConversations}>{t('loopx.tabConversations')}</button>
        </>}
    </section>
    <section className="goal-files-results" aria-label={t('loopx.files.reports')} aria-busy={busy}>
      <div className="goal-acceptance-head">
        <h2>{t('loopx.files.reports')}</h2>
        <button type="button" className="ghost" disabled={busy} onClick={() => void load()}>{t('loopx.files.refresh')}</button>
      </div>
      <p className="goal-acceptance-note">{t('loopx.files.reportsHint')}</p>
      {busy ? <p role="status" className="goal-acceptance-note">{t('loopx.files.verifying')}</p> : null}
      {error ? <p role="alert" className="goal-failure">{error}</p> : null}
      {page && page.unavailable > 0 ? <p role="status" className="goal-acceptance-note">{t('loopx.files.unavailable', page.unavailable)}</p> : null}
      {page && !busy && !page.items.length ? <p className="goal-empty">{page.nextCursor ? t('loopx.files.emptyPage') : t('loopx.files.empty')}</p> : null}
      {open ? <div className="goal-files-reader">
        <button type="button" className="ghost" onClick={() => setOpen(null)}>{t('loopx.files.back')}</button>
        <h3>{open.row.title}</h3>
        <p className="goal-acceptance-note">{open.row.producer} · <code>{open.row.todoId}</code></p>
        {open.row.contentType === 'text/markdown'
          ? <div className="goal-files-markdown"><Markdown remarkPlugins={[remarkGfm]}>{open.text}</Markdown></div>
          : <pre className="goal-files-text">{open.text}</pre>}
      </div> : <ul className="goal-files-list">
        {page?.items.map(row => <li key={resultKey(row)}>
          <button type="button" className="goal-files-row" disabled={busy} onClick={() => void select(row)}>
            <strong>{row.title}</strong>
            <small>{row.producer} · {row.todoId}</small>
            <span className="goal-files-meta">{t('loopx.files.accepted')}{row.completedAt && Number.isFinite(Date.parse(row.completedAt)) ? ` · ${new Date(row.completedAt).toLocaleString()}` : ''}</span>
          </button>
        </li>)}
        {page?.nextCursor ? <li><button type="button" className="ghost" disabled={busy} onClick={() => void load(page.nextCursor!)}>{t('loopx.files.next')}</button></li> : null}
      </ul>}
    </section>
  </div>;
}
