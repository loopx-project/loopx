import { useEffect, useState } from 'react';
import { ApiError, type Api, type ProjectDirectoryInfo, type WorkdirsConfig } from '../api';
import { folderName } from '../paths';
import { Dialog } from './Dialog';
import { IconChevron, IconFolder } from './icons';
import { t } from '../i18n';

interface DirectoryPickerProps {
  api: Api;
  allowedOnly?: boolean;
  /** A private setting may select its default without adding it to project roots. */
  defaultDirectory?: string;
  roots: string[];
  selected: string;
  machine: string;
  onRootsChange: (config: WorkdirsConfig) => void;
  onManageRoots: () => void;
  onSelect: (path: string) => void;
  onClose: () => void;
}

function explain(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 404 && error.message === 'not found') return t('dir.noBrowse');
    if (error.status === 404) return t('dir.notFound');
    if (error.status === 403) return t('dir.forbidden');
    if (/too wide/.test(error.message)) return t('dir.tooWide');
    if (/no such directory/.test(error.message)) return t('dir.notFound');
  }
  return error instanceof Error ? error.message : t('dir.unreadable');
}

export function DirectoryPicker({ api, allowedOnly = false, defaultDirectory, roots, selected, machine, onRootsChange, onManageRoots, onSelect, onClose }: DirectoryPickerProps) {
  // null shows configured roots; an empty path asks the daemon for its own home directory.
  const [at, setAt] = useState<string | null>(selected || null);
  const [info, setInfo] = useState<ProjectDirectoryInfo | null>(null);
  const [search, setSearch] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    if (at === null) return;
    let cancelled = false;
    const read = async () => {
      try {
        return await api.projectDirectories(at || undefined);
      } catch (e) {
        // Older daemons can still browse configured roots using their original endpoint.
        if (!(e instanceof ApiError) || e.status !== 404 || e.message !== 'not found' || !at ||
          !roots.some((root) => at === root || at.startsWith(root.replace(/\/$/, '') + '/'))) throw e;
        const old = await api.listDirectory(at);
        return {
          path: old.path,
          parent: old.parent && roots.some((root) => old.parent === root || old.parent!.startsWith(root.replace(/\/$/, '') + '/')) ? old.parent : null,
          directories: old.files.filter((f) => f.type === 'dir' && !f.name.startsWith('.') && f.name !== 'node_modules')
            .map((f) => ({ name: f.name, path: `${old.path.replace(/\/$/, '')}/${f.name}` })),
          allowed: true,
          selectable: true,
        };
      }
    };
    void read().then((value) => { if (!cancelled) setInfo(value); })
      .catch((e) => { if (!cancelled) setError(explain(e)); });
    return () => { cancelled = true; };
  }, [api, at, roots, retry]);

  const navigate = (path: string | null) => {
    setInfo(null);
    setError(null);
    setSearch('');
    setAt(path);
    setRetry((n) => n + 1);
  };
  const selectedDefault = Boolean(info && info.path === defaultDirectory);
  const useDirectory = async () => {
    if (!info?.selectable || (allowedOnly && !info.allowed && !selectedDefault) || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (!info.allowed && !selectedDefault) {
        try {
          onRootsChange(await api.addWorkdir(info.path));
        } catch (e) {
          // Another tab may have added this folder since the listing was loaded.
          if (!(e instanceof ApiError) || e.message !== 'already allowed') throw e;
          onRootsChange(await api.workdirs());
        }
      }
      onSelect(info.path);
      onClose();
    } catch (e) {
      setError(explain(e));
    } finally {
      setBusy(false);
    }
  };
  const folders = at === null ? roots.map((path) => ({ name: folderName(path), path })) : info?.directories ?? [];
  const shown = folders.filter((folder) => folder.name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));

  return (
    <Dialog
      title={t('dir.title')}
      className="project-browser"
      confirmLabel={busy ? t('dir.adding') : info && !info.allowed && !selectedDefault ? t('dir.addAndUse') : t('dir.use')}
      confirmDisabled={!info?.selectable || (allowedOnly && !info.allowed && !selectedDefault) || busy}
      onConfirm={() => void useDirectory()}
      onCancel={() => { if (!busy) onClose(); }}
    >
      <p className="project-browser-machine">{machine}</p>
      <div className="project-browser-controls" aria-busy={busy}>
        <div className="project-browser-nav">
          <button disabled={busy} type="button" className="ghost" onClick={() => navigate(null)}>{t('dir.available')}</button>
          {defaultDirectory && <button disabled={busy} type="button" className="ghost" onClick={() => navigate(defaultDirectory)}>{t('dir.defaultDirectory')}</button>}
          {!allowedOnly && <button disabled={busy} type="button" className="ghost" onClick={() => navigate('')}>{t('dir.browseHome')}</button>}
          {info?.parent && (!allowedOnly || roots.some(root => info.parent === root || info.parent!.startsWith(root.replace(/\/$/, '') + '/'))) && <button disabled={busy} type="button" className="ghost" onClick={() => navigate(info.parent)}>{t('dir.up')}</button>}
        </div>
        <p className="project-browser-path">{at === null ? t('dir.added') : info?.path || at || t('dir.openingHome')}</p>
        <input disabled={busy} aria-label={t('dir.search')} placeholder={t('dir.searchPlaceholder')} className="picker-search" value={search} onChange={(e) => setSearch(e.target.value)} />
        <div className="project-browser-list" aria-label={t('dir.folders')}>
          {at !== null && !info && !error ? <p className="project-browser-note" role="status">{t('dir.reading')}</p> : shown.map((folder) => (
            <button disabled={busy} type="button" className="project-browser-folder" key={folder.path} onClick={() => navigate(folder.path)}>
              <IconFolder size={19} />
              <span><span>{folder.name}</span>{at === null && <small>{folder.path}</small>}</span>
              <IconChevron size={14} />
            </button>
          ))}
          {(at === null || info) && shown.length === 0 && <p className="project-browser-note">{search ? t('dir.noMatch') : t('dir.empty')}</p>}
        </div>
        {error && <div className="project-browser-error" role="alert">{error} <button disabled={busy} type="button" className="ghost" onClick={() => navigate(at)}>{t('common.retry')}</button></div>}
        {!allowedOnly && info && !info.allowed && <p className="project-browser-note">{info.selectable ? t('dir.willAppear') : t('dir.enterProject')}</p>}
        {!allowedOnly && <button disabled={busy} type="button" className="ghost project-browser-manage" onClick={onManageRoots}>{t('dir.manual')}</button>}
      </div>
    </Dialog>
  );
}
