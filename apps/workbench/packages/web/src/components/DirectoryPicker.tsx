import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { ApiError, type Api, type ProjectDirectoryInfo, type WorkdirsConfig } from '../api';
import { folderName } from '../paths';
import { Dialog } from './Dialog';
import { IconCheck, IconChevron, IconFolder, IconSearch } from './icons';
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

/** True when the daemon itself has no such endpoint (an older one behind a proxy says so in its body). */
const missingEndpoint = (error: ApiError): boolean =>
  error.status === 404 && ((error.details as { error?: string } | undefined)?.error === 'not found' || error.message === 'not found');

function explain(error: unknown): string {
  if (error instanceof ApiError) {
    if (missingEndpoint(error)) return t('dir.noBrowse');
    if (error.status === 404) return t('dir.notFound');
    if (error.status === 403) return t('dir.forbidden');
    if (/too wide/.test(error.message)) return t('dir.tooWide');
    if (/no such directory/.test(error.message)) return t('dir.notFound');
    if (error.status === 400 && /absolute path/.test(error.message)) return t('dir.absolutePath');
  }
  return error instanceof Error ? error.message : t('dir.unreadable');
}

/** The folder lies inside one of the roots (or is one), so the person may work in it. */
const withinRoots = (path: string, roots: string[]) =>
  roots.some((root) => path === root || path.startsWith(root.replace(/\/$/, '') + '/'));

/** The root a path sits in, or null when it sits outside every configured root. */
const rootOf = (path: string, roots: string[]): string | null =>
  roots.find((root) => path === root || path.startsWith(root.replace(/\/$/, '') + '/')) ?? null;

interface Folder { name: string; path: string }

/**
 * Choosing where a thread works, as a tree the way GitHub and macOS's file lists show folders:
 * a folder's own name selects it (and opens it), the triangle beside it unfolds its children in
 * place, and a typed address jumps the tree to that folder. Nothing is hidden behind a step the
 * person has to discover: the configured roots and their first children are on screen at once.
 */
export function DirectoryPicker({ api, allowedOnly = false, defaultDirectory, roots, selected: initialSelected, machine, onRootsChange, onManageRoots, onSelect, onClose }: DirectoryPickerProps) {
  const [typed, setTyped] = useState('');
  const [selected, setSelected] = useState<string | null>(initialSelected || null);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [children, setChildren] = useState<Map<string, Folder[]>>(() => new Map());
  const [loading, setLoading] = useState<Set<string>>(() => new Set());
  const [nodeErrors, setNodeErrors] = useState<Map<string, string>>(() => new Map());
  // Folders a typed address led to, shown at the top of the tree beside the configured roots.
  const [extraRoots, setExtraRoots] = useState<string[]>([]);
  // A folder the daemon judged unusable as a work folder itself (the home directory, `/`).
  const [unselectable, setUnselectable] = useState<Set<string>>(() => new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  // What the input holds right now: a jump that lands late must not clear words typed since.
  const typedRef = useRef('');
  // A jump still in flight is dropped when the tree is sent back to its roots, or another jump
  // starts: a late answer must not reopen a branch the person left.
  const jumpSeq = useRef(0);
  useEffect(() => () => { alive.current = false; }, []);

  /** One folder's children, with the older daemon's browsing endpoint as the fallback for roots. */
  const readDirectory = (path: string): Promise<ProjectDirectoryInfo> =>
    api.projectDirectories(path).catch((e) => {
      if (!(e instanceof ApiError) || !missingEndpoint(e) || !withinRoots(path, roots)) throw e;
      return api.listDirectory(path).then((old) => ({
        path: old.path,
        parent: null,
        directories: old.files.filter((f) => f.type === 'dir' && !f.name.startsWith('.') && f.name !== 'node_modules')
          .map((f) => ({ name: f.name, path: `${old.path.replace(/\/$/, '')}/${f.name}` })),
        allowed: true,
        selectable: true,
      }));
    });

  /** Reads one folder and keeps what it says: its children, whether it is itself selectable. */
  const load = async (path: string): Promise<boolean> => {
    setLoading((prev) => new Set(prev).add(path));
    setNodeErrors((prev) => { const next = new Map(prev); next.delete(path); return next; });
    try {
      const info = await readDirectory(path);
      if (!alive.current) return false;
      setChildren((prev) => new Map(prev).set(path, info.directories));
      setUnselectable((prev) => {
        const next = new Set(prev);
        if (info.selectable) next.delete(path); else next.add(path);
        return next;
      });
      setExpanded((prev) => new Set(prev).add(path));
      return true;
    } catch (e) {
      if (alive.current) {
        setNodeErrors((prev) => new Map(prev).set(path, explain(e)));
        // The note with the reason (and its retry) sits under the folder, so it opens to show it.
        setExpanded((prev) => new Set(prev).add(path));
      }
      return false;
    } finally {
      if (alive.current) setLoading((prev) => { const next = new Set(prev); next.delete(path); return next; });
    }
  };

  const toggle = (path: string) => {
    if (loading.has(path)) return;
    if (nodeErrors.has(path)) { void load(path); return; }
    if (!expanded.has(path) && !children.has(path)) { void load(path); return; }
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path); else next.add(path);
      return next;
    });
  };

  /** Opens the tree down to a folder: its root first, then each step on the way. */
  const reveal = async (path: string) => {
    const root = rootOf(path, roots);
    if (!root) {
      if (!roots.includes(path) && !extraRoots.includes(path)) setExtraRoots((prev) => [path, ...prev]);
      await load(path);
      return;
    }
    const steps = [root];
    if (path !== root) {
      const rest = path.slice(root.replace(/\/$/, '').length + 1).split('/').filter(Boolean);
      let step = root.replace(/\/$/, '');
      for (const name of rest) { step = `${step}/${name}`; steps.push(step); }
    }
    for (const step of steps) if (!children.has(step)) await load(step);
    // Every folder on the way stays unfolded, whether its children were already read or not.
    setExpanded((prev) => { const next = new Set(prev); for (const step of steps) next.add(step); return next; });
  };

  // Open the tree on what the picker was opened for: the chosen folder's branch, or the first root.
  const rootsKey = roots.join('\n');
  const primed = useRef('');
  useEffect(() => {
    const key = `${initialSelected}\n${rootsKey}`;
    if (primed.current === key) return;
    primed.current = key;
    const start = initialSelected || '';
    if (start) { void reveal(start); return; }
    if (roots[0]) void load(roots[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, initialSelected, rootsKey]);

  /** A typed address opens that folder in the tree and selects it. */
  const jump = async (value: string) => {
    const seq = ++jumpSeq.current;
    setError(null);
    try {
      const info = await readDirectory(value);
      if (!alive.current || seq !== jumpSeq.current) return;
      const path = info.path;
      setChildren((prev) => new Map(prev).set(path, info.directories));
      setUnselectable((prev) => {
        const next = new Set(prev);
        if (info.selectable) next.delete(path); else next.add(path);
        return next;
      });
      setExpanded((prev) => new Set(prev).add(path));
      setExtraRoots((prev) => (roots.includes(path) || prev.includes(path) ? prev : [path, ...prev]));
      setSelected(path);
      if (typedRef.current === value) { typedRef.current = ''; setTyped(''); }
    } catch (e) {
      if (alive.current && seq === jumpSeq.current) setError(explain(e));
    }
  };

  /** The address bar's two readings: somewhere to go, or a word to narrow the tree with. */
  const isAddress = /^[/~]/.test(typed.trim());
  const word = !isAddress ? typed.trim().toLocaleLowerCase() : '';

  /** A folder's name selects it and unfolds it, one tap of progress; the triangle only unfolds. */
  const choose = (path: string) => {
    setSelected(path);
    if (children.get(path)?.length && !expanded.has(path)) setExpanded((prev) => new Set(prev).add(path));
    else if (!children.has(path) && !nodeErrors.has(path)) void load(path);
  };

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    const value = typed.trim();
    if (value && isAddress) void jump(value);
  };

  const target = selected;
  const targetAllowed = target !== null && withinRoots(target, roots);
  const selectedDefault = target !== null && target === defaultDirectory;
  const targetSelectable = target === null || !unselectable.has(target);
  const useDirectory = async () => {
    if (!target || !targetSelectable || (allowedOnly && !targetAllowed && !selectedDefault) || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (!targetAllowed && !selectedDefault) {
        try {
          onRootsChange(await api.addWorkdir(target));
        } catch (e) {
          // Another tab may have added this folder since the tree was loaded.
          if (!(e instanceof ApiError) || e.message !== 'already allowed') throw e;
          onRootsChange(await api.workdirs());
        }
      }
      onSelect(target);
      onClose();
    } catch (e) {
      setError(explain(e));
    } finally {
      setBusy(false);
    }
  };

  const resetToRoots = () => {
    jumpSeq.current++;
    typedRef.current = '';
    setTyped('');
    setExtraRoots([]);
    setExpanded(new Set());
    setSelected(null);
    setError(null);
  };

  const hint = error
    ? { kind: 'error' as const, text: error }
    : target && !targetSelectable
      ? { kind: 'guide' as const, text: t('dir.enterProject') }
      : target
        ? { kind: 'picked' as const, text: t('dir.pickedHint', folderName(target)) }
        : { kind: 'guide' as const, text: t('dir.treeHint') };

  const shownRoots = [...extraRoots, ...roots].filter((path, i, all) => all.indexOf(path) === i);
  const row = (path: string, name: string, depth: number, opts: { caret: boolean; open: boolean; filter?: boolean }) => {
    const picked = selected === path;
    return (
      <div className={`project-browser-row dir-tree-row${picked ? ' picked' : ''}`} style={{ paddingLeft: depth * 14 }}>
        {opts.caret ? (
          <button
            type="button"
            className={`dir-tree-caret${opts.open ? ' open' : ''}`}
            aria-label={t(opts.open ? 'dir.collapse' : 'dir.expand', name)}
            title={t(opts.open ? 'dir.collapse' : 'dir.expand', name)}
            aria-expanded={opts.open}
            onClick={() => toggle(path)}
          >
            <IconChevron size={13} />
          </button>
        ) : <span className="dir-tree-gap" aria-hidden="true" />}
        <button
          disabled={busy}
          type="button"
          className="project-browser-folder"
          title={path}
          aria-pressed={picked}
          onClick={() => { if (opts.filter) setSelected(path); else choose(path); }}
        >
          <IconFolder size={15} />
          <span>{name}</span>
          {depth === 0 && <small>{path}</small>}
        </button>
        {picked && <span className="dir-tree-check" aria-hidden="true"><IconCheck size={15} /></span>}
      </div>
    );
  };

  /** The tree as it stands: unfolded folders show their children, the rest wait for a tap. */
  const renderNode = (path: string, name: string, depth: number) => {
    const kids = children.get(path);
    const open = expanded.has(path);
    const isError = nodeErrors.has(path);
    const isLoading = loading.has(path);
    const caret = isError || kids === undefined || kids.length > 0;
    return (
      <div key={path}>
        {row(path, name, depth, { caret, open })}
        {open && (isLoading ? (
          <p className="project-browser-note dir-tree-note" role="status" style={{ paddingLeft: (depth + 1) * 14 + 30 }}>{t('dir.reading')}</p>
        ) : isError ? (
          <p className="project-browser-note dir-tree-note" role="alert" style={{ paddingLeft: (depth + 1) * 14 + 30 }}>
            {nodeErrors.get(path)}{' '}
            <button type="button" className="ghost" disabled={busy} onClick={() => void load(path)}>{t('common.retry')}</button>
          </p>
        ) : kids?.length ? kids.map((kid) => renderNode(kid.path, kid.name, depth + 1))
          : <p className="project-browser-note dir-tree-note" style={{ paddingLeft: (depth + 1) * 14 + 30 }}>{t('dir.empty')}</p>)}
      </div>
    );
  };

  /** While a word narrows the tree, every folder with a match under it stays on screen. */
  const filterNode = (path: string, name: string, depth: number, q: string): ReactNode => {
    const kids = children.get(path) ?? [];
    const selfMatch = name.toLocaleLowerCase().includes(q);
    const below = selfMatch ? [] : kids.map((kid) => filterNode(kid.path, kid.name, depth + 1, q)).filter(Boolean);
    if (!selfMatch && below.length === 0) return null;
    return (
      <div key={path}>
        {row(path, name, depth, { caret: false, open: false, filter: true })}
        {selfMatch
          ? kids.map((kid) => filterNode(kid.path, kid.name, depth + 1, q)).filter(Boolean)
          : below}
      </div>
    );
  };

  const filtered = word
    ? shownRoots.map((path) => filterNode(path, folderName(path), 0, word)).filter(Boolean)
    : [];

  return (
    <Dialog
      title={t('dir.title')}
      className="project-browser"
      confirmLabel={busy ? t('dir.adding') : target && !targetAllowed && !selectedDefault ? t('dir.addAndUse') : target ? t('dir.selectFolder', folderName(target)) : t('dir.use')}
      confirmDisabled={!target || !targetSelectable || (allowedOnly && !targetAllowed && !selectedDefault) || busy}
      onConfirm={() => void useDirectory()}
      onCancel={() => { if (!busy) onClose(); }}
    >
      <div className="project-browser-controls" aria-busy={busy}>
        <div className="project-browser-title">
          <h2>{t('dir.title')}</h2>
          <span className="project-browser-machine">{machine}</span>
        </div>
        <form className="project-browser-goto" onSubmit={submit}>
          <IconSearch size={14} />
          <input
            disabled={busy}
            aria-label={t('dir.pathLabel')}
            placeholder={t('dir.enterPath')}
            className="project-browser-input"
            value={typed}
            spellCheck={false}
            autoCapitalize="off"
            autoComplete="off"
            onChange={(e) => { typedRef.current = e.target.value; setTyped(e.target.value); setError(null); }}
          />
          {isAddress && typed.trim() && (
            <button disabled={busy} type="submit" className="project-browser-go">{t('dir.go')}</button>
          )}
        </form>
        <p className={`project-browser-hint${hint.kind === 'error' ? ' error' : ''}`} role={hint.kind === 'error' ? 'alert' : undefined}>
          {hint.text}
        </p>
        <div className="project-browser-list dir-tree" role="tree" aria-label={t('dir.folders')}>
          <p className="project-browser-added">{t('dir.added')}</p>
          {word
            ? (filtered.length ? filtered : <p className="project-browser-note">{t('dir.noMatch')}</p>)
            : shownRoots.map((path) => renderNode(path, folderName(path), 0))}
        </div>
        <div className="project-browser-foot">
          <div className="project-browser-foot-links">
            {roots.length > 0 && (
              <button disabled={busy} type="button" className="ghost" onClick={resetToRoots}>{t('dir.available')}</button>
            )}
            {defaultDirectory && (
              <button disabled={busy} type="button" className="ghost" onClick={() => { setSelected(defaultDirectory); void reveal(defaultDirectory); }}>{t('dir.defaultDirectory')}</button>
            )}
            {!allowedOnly && <button disabled={busy} type="button" className="ghost" onClick={onManageRoots}>{t('dir.manual')}</button>}
          </div>
          <span className="project-browser-where" title={target ?? undefined}>{target ?? ''}</span>
        </div>
      </div>
    </Dialog>
  );
}
