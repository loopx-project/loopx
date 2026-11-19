import { useEffect, useState } from 'react';
import type { Api, Thread, Todo, TodoNote, Wish } from '../api';
import { t } from '../i18n';
import { Popover } from './Popover';
import { IconChevron } from './icons';

// Section heading in the daemon's continuation-note protocol, independent of UI language.
const NEXT_STEP_HEADING = '下一步';
export const personalTime = (value: string) => new Date(value).toLocaleString([], { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
export function TodoMetadata({ todo }: { todo: Todo }) {
  const overdue = todo.dueAt && !todo.done && Date.parse(todo.dueAt) < Date.now();
  return <span className="todo-metadata">
    {todo.waitingOn && <span>{t('personal.waiting', todo.waitingOn)}</span>}
    {todo.dueAt && <span className={overdue ? 'personal-warning' : ''}>{overdue ? t('personal.overdue', Math.max(1, Math.floor((Date.now() - Date.parse(todo.dueAt)) / 86400000))) : t('personal.due', new Date(todo.dueAt).toLocaleDateString([], { month: 'numeric', day: 'numeric' }))}</span>}
    {todo.followUpPaused ? <span className="personal-warning">{t('personal.paused')}</span> : todo.followUpAt && <span>{t('personal.followUp', personalTime(todo.followUpAt))}</span>}
  </span>;
}

/** Notes are fetched only while expanded; a revision change invalidates the open view. */
export function TodoDetails({ api, todo, onReload, onOpenThread }: { api: Api; todo: Todo; onReload: () => void; onOpenThread: (id: string) => void }) {
  const [note, setNote] = useState<TodoNote | null>(null);
  const [error, setError] = useState('');
  const [saveError, setSaveError] = useState('');
  const [retry, setRetry] = useState(0);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true; setNote(null); setError('');
    void api.todoNote(todo.id).then(v => { if (live) setNote(v); }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, [api, todo.id, todo.noteUpdatedAt, retry]);
  // Headings are protocol text written by agents, so preserve them rather than translating.
  const sections: Array<{ title: string; lines: string[] }> = [];
  if (note) for (const line of note.text.split(/\r?\n/)) {
    const heading = line.match(/^\s*#{1,6}\s+(.+)$/);
    if (heading) sections.push({ title: heading[1].trim(), lines: [] });
    else { if (!sections.length) sections.push({ title: '', lines: [] }); sections.at(-1)!.lines.push(line); }
  }
  sections.sort((a, b) => Number(b.title === NEXT_STEP_HEADING) - Number(a.title === NEXT_STEP_HEADING));
  const source = todo.threads.find(thread => thread.id === note?.threadId);
  const followUp = async (preset: string, close: () => void) => {
    const date = new Date();
    if (preset === 'later') date.setHours(date.getHours() + 3);
    if (preset === 'tomorrow') { date.setDate(date.getDate() + 1); date.setHours(9, 0, 0, 0); }
    if (preset === 'threeDays') date.setDate(date.getDate() + 3);
    if (preset === 'monday') { date.setDate(date.getDate() + ((8 - date.getDay()) % 7 || 7)); date.setHours(9, 0, 0, 0); }
    setBusy(true); setSaveError('');
    try { await api.updateTodo(todo.id, { followUpAt: preset === 'clear' ? null : date.toISOString() }); close(); onReload(); }
    catch (e) { setSaveError(String(e)); } finally { setBusy(false); }
  };
  return <div className="todo-details">
    {!note && !error && <p role="status">{t('common.loading')}</p>}
    {error && <p role="alert">{error} <button className="ghost" onClick={() => setRetry(n => n + 1)}>{t('common.retry')}</button></p>}
    {note && <>{note.text.trim() ? sections.map((section, i) => <div className="todo-note-section" key={i}>{section.title && <strong>{section.title}</strong>}<span>{section.lines.join('\n').trim()}</span></div>) : null}
    </>}
    <div className="todo-note-source">{note && !note.text.trim() && <>{t('personal.noNote')} · </>}{note?.text.trim() && note.updatedAt && <>{t('personal.updated', personalTime(note.updatedAt))}{source && <> · <button className="text-btn" onClick={() => onOpenThread(source.id)}>{source.title}</button></>} · </>}
      <Popover label={t(todo.followUpAt ? 'personal.changeFollowUp' : 'personal.setFollowUp')} triggerClassName="text-btn" trigger={t(todo.followUpAt ? 'personal.changeFollowUp' : 'personal.setFollowUp')} disabled={busy} placement="below">
        {close => <>{(['later', 'tomorrow', 'threeDays', 'monday', 'clear'] as const).map(preset => <button key={preset} className="pop-item compact" role="menuitem" onClick={() => void followUp(preset, close)}>{t(`personal.${preset}`)}</button>)}</>}
      </Popover>
    </div>{saveError && <p className="personal-warning" role="alert">{saveError}</p>}
  </div>;
}

export function Wishes({ api, count, threads, onOpenThread }: { api: Api; count: number; threads: Thread[]; onOpenThread: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  const [value, setValue] = useState<Wish[] | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!expanded) return; let live = true; setValue(null); setError('');
    void api.wishes().then(v => { if (live) setValue(v); }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, [api, expanded, count, retry]);
  if (!count) return null;
  return <div className="personal-wishes"><button className="todos-done-head" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}><IconChevron size={13} open={expanded} />{t('personal.wishes', count)}</button>
    {expanded && <div className="personal-wish-list">{!value && !error && <p>{t('common.loading')}</p>}{error && <p role="alert">{error} <button className="ghost" onClick={() => setRetry(n => n + 1)}>{t('common.retry')}</button></p>}{value?.map(wish => {
      const source = threads.find(thread => thread.id === wish.threadId);
      return <div key={wish.id}><p>{wish.text}</p><small>{personalTime(wish.createdAt)} · <button className="text-btn" onClick={() => onOpenThread(wish.threadId)}>{t('personal.source', source?.title || t('sidebar.threads'))}</button></small></div>;
    })}</div>}
  </div>;
}
