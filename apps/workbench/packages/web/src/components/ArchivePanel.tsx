import { useEffect, useState } from 'react';
import type { AgentInfo, Api, Thread } from '../api';
import { folderName } from '../paths';
import { IconSearch } from './icons';
import { formatDate, t } from '../i18n';
import { SettingsSection } from './PageNavigation';

interface Props {
  api: Api;
  agents: AgentInfo[];
  /** Changes whenever a thread was archived, restored or deleted elsewhere, so the list reloads. */
  refreshKey: number;
  onRestore: (thread: Thread) => void;
  onDelete: (thread: Thread) => void;
  onError: (e: unknown) => void;
}

function archivedOn(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return t('archive.archivedOn', formatDate(iso, { month: 'short', day: 'numeric' }));
}

/** Archived threads: out of the sidebar, still on disk, one tap from coming back. */
export function ArchivePanel({ api, agents, refreshKey, onRestore, onDelete, onError }: Props) {
  const [threads, setThreads] = useState<Thread[] | null>(null);
  const [search, setSearch] = useState('');
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let alive = true;
    setError('');
    api
      .threads({ archived: true })
      .then((list) => alive && setThreads(list))
      .catch((e) => { if (alive) { setError(e instanceof Error ? e.message : String(e)); onError(e); } });
    return () => {
      alive = false;
    };
    // onError only reports; it is not a reason to reload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, refreshKey, retry]);


  const query = search.trim().toLowerCase();
  const shown = (threads ?? []).filter((t) => !query || t.title.toLowerCase().includes(query) || t.cwd.toLowerCase().includes(query));
  const label = (id: string) => agents.find((a) => a.id === id)?.label ?? id;

  return (
    <SettingsSection title={t('archive.title')} hint={t('archive.hint')} className="history-settings">
        <label className="panel-search">
          <IconSearch size={16} />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('archive.search')}
            aria-label={t('archive.search')}
          />
        </label>

        <div className="panel-list">
          {error && <p className="panel-error" role="alert">{error} <button className="text-btn" onClick={() => setRetry(value => value + 1)}>{t('common.retry')}</button></p>}
          {threads === null && !error && <p className="panel-empty">{t('common.loading')}</p>}
          {threads !== null && shown.length === 0 && (
            <p className="panel-empty">{threads.length === 0 ? t('archive.empty') : t('archive.noMatch')}</p>
          )}
          {shown.map((thread) => (
            <div key={thread.id} className="panel-row">
              <div className="panel-row-main">
                <div className="panel-row-title">{thread.title}</div>
                <div className="panel-row-meta">
                  <span className="tag">{label(thread.agent)}</span>
                  <span>{folderName(thread.cwd)}</span>
                  <span>{archivedOn(thread.archivedAt)}</span>
                </div>
              </div>
              <button className="text-btn" onClick={() => onRestore(thread)}>
                {t('archive.restore')}
              </button>
              <button className="text-btn danger" onClick={() => onDelete(thread)}>
                {t('archive.delete')}
              </button>
            </div>
          ))}
        </div>
    </SettingsSection>
  );
}
