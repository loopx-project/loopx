import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { Api, ProactiveAction, ProactiveFile, ProactiveRun, Todo } from '../api';
import { formatDateTime, t, type MessageKey } from '../i18n';
import { diffTotals, fileName, proactiveMatters, replaceRun, runControls } from '../proactive';
import { Dialog, DialogSurface } from './Dialog';
import { IconFile, IconSparkle } from './icons';

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

export interface ProactiveState {
  /** null until asked for and answered. */
  runs: ProactiveRun[] | null;
  error: string;
  load: () => void;
  /** The action in flight, per run. */
  pending: Record<string, ProactiveAction>;
  /** Why the last action on a run failed; the buttons stay, so it can be tried again. */
  failures: Record<string, string>;
  act: (run: ProactiveRun, action: ProactiveAction, answer?: string) => void;
}

/** Her runs, read only for what is on screen: one matter's (`scope` its id), all of them (''), or none (null). */
export function useProactive(api: Api, scope: string | null, refreshKey: number, onAction?: () => void): ProactiveState {
  const [runs, setRuns] = useState<ProactiveRun[] | null>(null);
  const [error, setError] = useState('');
  const [pending, setPending] = useState<Record<string, ProactiveAction>>({});
  const [failures, setFailures] = useState<Record<string, string>>({});
  const asked = useRef(0);
  const currentScope = useRef(scope);
  const load = useCallback(() => {
    const ticket = ++asked.current;
    setError('');
    void api.proactive(scope || undefined).then(value => { if (ticket === asked.current) setRuns(value.runs); }).catch(e => { if (ticket === asked.current) setError(message(e)); });
  }, [api, scope]);
  useEffect(() => { currentScope.current = scope; asked.current++; setRuns(null); setError(''); }, [scope]);
  useEffect(() => { if (scope !== null) load(); }, [scope, refreshKey, load]);
  const act = (run: ProactiveRun, action: ProactiveAction, answer?: string) => {
    if (pending[run.id]) return;
    setPending(now => ({ ...now, [run.id]: action }));
    setFailures(({ [run.id]: _gone, ...rest }) => rest);
    void api.proactiveAct(run.id, action, answer)
      // An answer to a read begun before the action is out of date.
      .then(next => { if (currentScope.current === scope) { asked.current++; setRuns(now => now && replaceRun(now, next)); } onAction?.(); })
      .catch(e => { setFailures(now => ({ ...now, [run.id]: message(e) })); if (currentScope.current === scope) load(); })
      .finally(() => setPending(({ [run.id]: _done, ...rest }) => rest));
  };
  return { runs, error, load, pending, failures, act };
}

/** The changed files with their line counts. The list is only read once this opens. */
function DiffDialog({ api, run, onClose }: { api: Api; run: ProactiveRun; onClose: () => void }) {
  const [files, setFiles] = useState<ProactiveFile[] | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let live = true;
    setError('');
    void api.proactiveDiff(run.id).then(value => { if (live) setFiles(value.files); }).catch(e => { if (live) setError(message(e)); });
    return () => { live = false; };
  }, [api, run.id, retry]);
  const totals = files && diffTotals(files);
  return createPortal(<DialogSurface className="proactive-diff" labelledBy="proactive-diff-title" onClose={onClose}>
    <h2 id="proactive-diff-title" className="dialog-title">{t('proactive.changes')}</h2>
    {files === null && !error && <p role="status">{t('common.loading')}</p>}
    {error && <p className="panel-error" role="alert">{error} <button className="ghost" onClick={() => setRetry(n => n + 1)}>{t('common.retry')}</button></p>}
    {files?.length === 0 && <p className="desk-empty">{t('proactive.noChanges')}</p>}
    {files && files.length > 0 && <>
      <p className="proactive-diff-total">{t('proactive.fileCount', files.length)}<DiffCount additions={totals!.additions} deletions={totals!.deletions} /></p>
      <ul className="proactive-diff-files">{files.map(file => <li key={file.path}><span className="proactive-diff-path">{file.path}</span><DiffCount additions={file.additions} deletions={file.deletions} /></li>)}</ul>
    </>}
    <div className="dialog-actions"><button className="ghost" onClick={onClose}>{t('common.close')}</button></div>
  </DialogSurface>, document.body);
}

function DiffCount({ additions, deletions }: { additions: number; deletions: number }) {
  return <span className="diff-stats"><span className="diff-added">+{additions}</span><span className="diff-removed">-{deletions}</span></span>;
}

/** System choices are protocol values the daemon parses back; translate only their labels, never the answer sent. */
const SYSTEM_ANSWERS: Record<string, Record<string, MessageKey>> = { transient: { 再试一次: 'proactive.retryAgain', 先放着: 'proactive.leaveForNow' }, external: { 继续等: 'proactive.keepWaiting', 不等了: 'proactive.stopWaiting' } };

function answerLabel(run: ProactiveRun, option: string): string {
  const key = SYSTEM_ANSWERS[run.reason ?? '']?.[option];
  return key ? t(key) : option;
}

/** What can be done about a run, wherever it shows. Discarding is the one thing that cannot be undone, so it asks. */
/** `hideQuestion`: the caller already shows the question in full, so it is not said twice. */
export function RunControls({ api, run, state, hideQuestion = false }: { api: Api; run: ProactiveRun; state: ProactiveState; hideQuestion?: boolean }) {
  const [viewing, setViewing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [answer, setAnswer] = useState('');
  const options = run.question?.options ?? [];
  // With options to pick from, writing an answer of one's own is one tap away rather than always open.
  const [writing, setWriting] = useState(!options.length);
  const pending = state.pending[run.id];
  const controls = runControls(run);
  if (!controls.length) return null;
  if (controls.includes('answer')) return <div className="proactive-answer">
    {run.question?.text && !hideQuestion && <p>{run.question.text}</p>}
    {(options.length > 0 || !writing) && <div className="desk-card-actions">
      {options.map(option => <button key={option} className="ghost" disabled={Boolean(pending)} onClick={() => state.act(run, 'answer', option)}>{answerLabel(run, option)}</button>)}
      {!writing && <button className="ghost quiet" disabled={Boolean(pending)} aria-expanded={false} onClick={() => setWriting(true)}>{t('proactive.otherAnswer')}</button>}
    </div>}
    {writing && <form className="desk-card-actions proactive-answer-form" aria-label={t('proactive.otherAnswer')} onSubmit={e => { e.preventDefault(); if (answer.trim()) state.act(run, 'answer', answer.trim()); }}>
      <input aria-label={t('proactive.otherAnswer')} placeholder={t('proactive.otherAnswer')} value={answer} disabled={Boolean(pending)} autoFocus={options.length > 0} onChange={e => setAnswer(e.target.value)} />
      <button className="ghost" disabled={Boolean(pending) || !answer.trim()}>{t('proactive.answer')}</button>
    </form>}
  </div>;
  const label = (control: Exclude<typeof controls[number], 'diff'>) => {
    if (control === 'dismiss') return t(run.kind === 'dispatch' ? 'proactive.noThanks' : run.needsRepo ? 'proactive.declineRepo' : run.reason === 'done' ? 'proactive.notDone' : 'proactive.stopWaiting');
    return t(`proactive.${control}`);
  };
  return <>
    <div className="desk-card-actions">{controls.map(control => control === 'diff'
      ? <button key={control} className="ghost" onClick={() => setViewing(true)}>{t('proactive.viewChanges')}</button>
      : <button key={control} className={control === 'merge' || control === 'start' ? 'primary' : 'ghost'} disabled={Boolean(pending)} aria-busy={pending === control} onClick={() => { if (control === 'discard') setConfirming(true); else state.act(run, control); }}>{label(control)}</button>)}
    </div>
    {viewing && <DiffDialog api={api} run={run} onClose={() => setViewing(false)} />}
    {confirming && createPortal(<Dialog title={t('proactive.discardTitle')} message={t('proactive.discardHint')} confirmLabel={t('proactive.discard')} danger onCancel={() => setConfirming(false)} onConfirm={() => { setConfirming(false); state.act(run, 'discard'); }} />, document.body)}
  </>;
}

const statusLabel = (run: ProactiveRun): string => {
  if (run.needsRepo && run.status === 'observed') return t('proactive.allowSandbox');
  if (run.status === 'asking' && run.reason === 'done') return t('proactive.looksDone');
  if (run.status === 'ready') return t(run.kind === 'sandbox' ? 'proactive.status.sandboxReady' : 'proactive.status.prepared');
  if (run.status === 'running') return t(run.kind === 'sandbox' ? 'proactive.status.sandboxRunning' : 'proactive.status.preparing');
  return t(`proactive.status.${run.status}`);
};

/** One card in a matter's spread: quiet for materials, a decision for finished sandbox work, the reason for a failure. */
export function ProactiveCard({ api, run, state, style }: { api: Api; run: ProactiveRun; state: ProactiveState; style?: React.CSSProperties }) {
  const failure = state.failures[run.id];
  const failed = run.status === 'failed';
  const prepared = run.kind === 'prepare' && run.status === 'ready';
  return <article className={`desk-card proactive-card ${run.status}${prepared ? ' prepared' : ''}`} data-run={run.id} style={style}>
    <span className={`desk-card-kind${failed ? ' personal-warning' : ''}`}>{prepared ? <IconFile size={14} /> : <IconSparkle size={14} />}{statusLabel(run)}</span>
    {prepared && (run.files?.length ?? 0) > 0 && <ul className="proactive-files">{run.files!.map(file => <li key={file.path} title={file.path}>{fileName(file.path)}</li>)}</ul>}
    <RunSummary run={run} />
    {run.kind === 'sandbox' && run.status === 'ready' && run.testReport && <p className="proactive-tests">{run.testReport}</p>}
    {run.kind === 'sandbox' && run.status === 'ready' && run.files && <small className="desk-card-foot">{t('proactive.fileCount', run.files.length)}</small>}
    <RunControls key={run.id} api={api} run={run} state={state} />
    {failure && <p className="desk-action-error proactive-failure" role="alert">{failure}</p>}
  </article>;
}

export function RunSummary({ run }: { run: ProactiveRun }) {
  if (run.status === 'waiting') return <p>{t('proactive.waitingFor', run.wait?.what || run.line || run.summary || t('proactive.service'))}{run.wakeAt && Number.isFinite(Date.parse(run.wakeAt)) && <> · {t('proactive.checkAt', formatDateTime(run.wakeAt, { hour: '2-digit', minute: '2-digit', hour12: false }))}</>}</p>;
  if (run.status === 'asking' && run.reason !== 'done' && run.question?.text) return null;
  const summary = run.status === 'asking' ? run.line || run.summary : run.summary;
  return <>{(summary || run.status === 'failed') && <p>{summary || t('proactive.failedHint')}</p>}{run.needsRepo && run.status === 'observed' && run.repo && <p className="proactive-tests">{run.repo}</p>}</>;
}

/** The filter's answer: one line per matter she acted on, with what is left to decide right on it. */
export function ProactiveList({ api, todos, state, canOpen, onOpen, onOpenThread, askingOnly = false }: { api: Api; todos: Todo[]; state: ProactiveState; canOpen: (id: string) => boolean; onOpen: (id: string, source: HTMLElement) => void; onOpenThread?: (id: string) => void; askingOnly?: boolean }) {
  if (state.error) return <p className="desk-state" role="alert">{state.error} <button className="ghost" onClick={state.load}>{t('common.retry')}</button></p>;
  if (!state.runs) return <p className="desk-state" role="status">{t('common.loading')}</p>;
  const matters = proactiveMatters(askingOnly ? state.runs.filter(run => run.status === 'asking') : state.runs, todos);
  if (!matters.length) return <p className="desk-empty">{t(askingOnly ? 'proactive.noneAsking' : 'proactive.none')}</p>;
  return <ul className="proactive-list desk-paper">{matters.map(({ matterId, todo, run }) => <li key={matterId} data-matter={matterId}>
    <div className="proactive-row-main">
      {todo && canOpen(matterId) ? <button className="proactive-row-title" onClick={e => onOpen(matterId, e.currentTarget)}>{todo.text}</button>
        : !run.matterId && run.threadId && onOpenThread ? <button className="proactive-row-title" onClick={() => onOpenThread(run.threadId)}>{run.threadTitle || t('proactive.untitledSession')}</button>
        : <span className="proactive-row-title">{todo?.text ?? (run.matterId ? run.summary ?? t('proactive.goneMatter') : run.threadTitle || t('proactive.untitledSession'))}</span>}
      <span className={`proactive-row-status${run.status === 'failed' || run.status === 'asking' ? ' personal-warning' : ''}`}>{statusLabel(run)}</span>
    </div>
    {(run.kind === 'advance' || run.needsRepo) && <RunSummary run={run} />}
    <RunControls key={run.id} api={api} run={run} state={state} />
    {state.failures[run.id] && <p className="desk-action-error proactive-failure" role="alert">{state.failures[run.id]}</p>}
  </li>)}</ul>;
}
