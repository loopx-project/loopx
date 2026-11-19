import { useEffect, useState } from 'react';
import type { Api, PersonalSettings, Thread, Todo } from '../api';
import { t } from '../i18n';
import { linkableThreads } from '../todos';
import { Dialog } from './Dialog';
import { DirectoryPicker } from './DirectoryPicker';
import { Popover } from './Popover';
import { IconChevron, IconSearch } from './icons';

export type DelegateRequest = { id?: string; text?: string; cwd?: string };

/** The same three destinations for a draft and an existing plan. */
export function TodoDelegate({ api, personal, threads, request, busy, draft, panelClassName, onDelegate, onLink }: {
  api: Api; personal: PersonalSettings | null; threads: Thread[]; request: DelegateRequest; busy: boolean; draft?: boolean; panelClassName?: string;
  onDelegate: (request: DelegateRequest) => void; onLink: (request: DelegateRequest) => void;
}) {
  const [browse, setBrowse] = useState<string[] | null>(null);
  return <>
    <Popover panelClassName={panelClassName} label={t('personal.delegate')} placement="below" align="end" triggerClassName={draft ? 'todo-delegate-new' : 'todo-delegate'} disabled={busy} trigger={t('personal.delegate')}>
      {close => <Destinations api={api} personal={personal} threads={threads} onPrivate={() => { close(); onDelegate(request); }} onProject={cwd => { close(); onDelegate({ ...request, cwd }); }} onBrowse={roots => { close(); setBrowse(roots); }} onLink={() => { close(); onLink(request); }} />}
    </Popover>
    {browse && <DirectoryPicker api={api} allowedOnly roots={browse} defaultDirectory={personal?.defaultDirectory} selected={browse[0] ?? personal?.directory ?? ''} machine="" onRootsChange={() => {}} onManageRoots={() => {}} onSelect={cwd => { setBrowse(null); onDelegate({ ...request, cwd }); }} onClose={() => setBrowse(null)} />}
  </>;
}

function Destinations({ api, personal, threads, onPrivate, onProject, onBrowse, onLink }: {
  api: Api; personal: PersonalSettings | null; threads: Thread[]; onPrivate: () => void; onProject: (cwd: string) => void; onBrowse: (roots: string[]) => void; onLink: () => void;
}) {
  const [projects, setProjects] = useState(false);
  const [roots, setRoots] = useState<string[] | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!projects) return;
    let live = true; setError('');
    void api.workdirs().then(v => { if (live) setRoots(v.workdirs); }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, [api, projects, retry]);
  if (!projects) return <>
    <button className="pop-item compact" role="menuitem" onClick={onPrivate}>{t('personal.newPrivate')}</button>
    <button className="pop-item compact" role="menuitem" onClick={() => setProjects(true)}><span className="pop-item-text">{t('personal.inProject')}</span><span className="pop-item-check"><IconChevron size={12} /></span></button>
    <button className="pop-item compact" role="menuitem" onClick={onLink}>{t('personal.linkExisting')}</button>
  </>;
  const allowed = [...(roots ?? []), ...(personal?.directory ? [personal.directory] : [])];
  const recent = [...new Set([...threads].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)).map(thread => thread.cwd))]
    .filter(cwd => allowed.some(root => cwd === root || cwd.startsWith(root.replace(/\/$/, '') + '/'))).slice(0, 5);
  return <>
    <button className="pop-back" role="menuitem" onClick={() => setProjects(false)}><IconChevron size={12} />{t('personal.delegate')}</button>
    {!roots && !error && <p role="status">{t('common.loading')}</p>}
    {error && <p role="alert">{error} <button className="ghost" onClick={() => setRetry(n => n + 1)}>{t('common.retry')}</button></p>}
    {roots && <>{recent.map(cwd => <button key={cwd} className="pop-item compact" role="menuitem" onClick={() => onProject(cwd)}><span className="pop-item-label" title={cwd}>{cwd}</span></button>)}
      <button className="pop-item compact" role="menuitem" onClick={() => onBrowse(roots)}>{t('personal.chooseProjectDirectory')}</button></>}
  </>;
}

/** Used by both the row's link action and the delegation destination menu. */
export function TodoThreadPicker({ todos, threads, todoId, busy, onPick, onClose }: {
  todos: Todo[]; threads: Thread[]; todoId?: string; busy: boolean; onPick: (threadId: string) => void; onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const choices = new Set(linkableThreads(todos, todoId ?? '', threads).map(thread => thread.id));
  const shown = threads.filter(thread => choices.has(thread.id) && thread.title.toLowerCase().includes(query.trim().toLowerCase())).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  return <Dialog title={t('personal.linkExistingTitle')} className="todo-thread-picker" confirmLabel={t('common.close')} hideCancel onConfirm={onClose} onCancel={onClose}>
    <label className="pop-search"><IconSearch size={15} /><input value={query} aria-label={t('sidebar.search')} placeholder={t('sidebar.search')} onChange={e => setQuery(e.target.value)} /></label>
    <div className="pop-scroll">{shown.map(thread => <button key={thread.id} className="pop-item compact" disabled={busy} onClick={() => onPick(thread.id)}>{thread.title || thread.cwd}</button>)}{!shown.length && <p className="pop-note">{t('projects.noMatch')}</p>}</div>
  </Dialog>;
}
