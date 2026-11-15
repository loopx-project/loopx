import { Children, isValidElement, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import Markdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Api, FolderEntry, FolderListing, TextFile, WritingStage } from '../api';
import { countWords, highlightWritingSource, isArticleFile, rehypeArticleMarks } from '../article';
import { copyText } from '../clipboard';
import { crumbsOf, escapeHtml, foldersTo, languageOf, parentDir, resolveRelative, sizeLabel, splitHighlighted } from '../files';
import { pictureUrl, saveAs } from '../images';
import { folderName, IMAGE_FILE, PDF_FILE, VIDEO_FILE } from '../paths';
import { enlargeMermaidDiagram, renderMermaidDiagram } from '../mermaid';
import { conflictHash, readBodyDraft, relativePicture, stageLabel, writeBodyDraft } from '../writing';
import { Dialog } from './Dialog';
import { Lightbox } from './Lightbox';
import { Popover } from './Popover';
import { IconChevron, IconClose, IconCopy, IconDownload, IconFile, IconFolder, IconFolders, IconMore, IconSearch, IconSidebar, IconWrap } from './icons';
import { t } from '../i18n';

/** What the panel is asked to show: a file (at a line), a folder in the tree, or with no path, the thread's folder. */
export interface FilesTarget {
  path: string | null;
  line?: number;
  /** Already fetched by whoever opened the panel, so it is not asked for twice. */
  file?: TextFile;
  /** A folder to stand the tree in, as when an answer's link names a directory. */
  folder?: string;
  /** A new number each time something is opened, so opening the same file again scrolls to it again. */
  seq: number;
}

interface Props {
  api: Api;
  threadId: string;
  target: FilesTarget;
  /** A phone: one full-screen page that switches between the file and its folder. */
  phone: boolean;
  /** The thread's writing stage, for the line under the article's title. */
  writingStage?: WritingStage | null;
  /** Refresh the visible file after a thread update; drafts keep their original base. */
  revision?: string;
  /** The person's writing habits note: shown with the body's article typography, no stage. */
  writingHabits?: string | null;
  /** Notes picked out of this panel and waiting in the composer. On a phone the panel covers the
   * box, so they are counted at the bottom until the writer goes back to send them. */
  noted?: { count: number; nonce: number } | null;
  onClose: () => void;
}

type Shown =
  | { state: 'loading'; path: string }
  | { state: 'text'; file: TextFile }
  | { state: 'picture'; file: TextFile; url: string }
  | { state: 'video'; file: TextFile; url: string }
  | { state: 'pdf'; file: TextFile; url: string }
  | { state: 'error'; path: string; message: string };

/**
 * A writing-mode body open as plain text. `baseHash` is the file the text started from; `conflict`
 * is what is on disk instead, once a save found the file changed.
 */
interface Editing {
  text: string;
  baseHash: string;
  saving: boolean;
  conflict: string | null;
  error: string | null;
}

const WIDTH_KEY = 'calli.files.width';
const WRAP_KEY = 'calli.files.wrap';
const TREE_KEY = 'calli.files.tree';
/** The conversation keeps at least this much of the window: its composer's bottom row — the
 * collaboration button, the model's name — with nothing cut short. */
const MIN_CHAT = 440;
const MIN_WIDTH = 360;
/** The sidebar's width while it is open; the panel is capped before the app is first laid out. */
const SIDEBAR_W = 288;

function savedWidth(): number {
  const saved = Number(localStorage.getItem(WIDTH_KEY));
  return saved > 0 ? saved : Math.round(window.innerWidth * 0.45);
}
/** What is left of the window once the conversation has its share: sidebar (folded away is gone)
 * and all. The panel never takes more than this, not by opening a body and not by dragging. */
function maxPanelWidth(): number {
  const sidebar = document.querySelector('.app > .sidebar');
  const open = sidebar ? getComputedStyle(sidebar).visibility !== 'hidden' : window.innerWidth >= 900;
  const taken = sidebar && open ? sidebar.getBoundingClientRect().width : open ? SIDEBAR_W : 0;
  return Math.max(MIN_WIDTH, window.innerWidth - Math.round(taken) - MIN_CHAT);
}
const clampWidth = (w: number) => Math.max(MIN_WIDTH, Math.min(w, maxPanelWidth()));

// The highlighter is a few dozen KB; it is fetched the first time a file is shown, then kept.
let highlighter: Promise<typeof import('../highlight')> | null = null;
const loadHighlighter = () => (highlighter ??= import('../highlight'));

/**
 * Looking through the thread's folder, read only, like Codex's file panel: a file with line numbers
 * and highlighting, and the folder tree beside it. On a computer it sits to the right of the
 * conversation; on a phone it is a page of its own, with the tree one step back from the file.
 */
export function FilesPanel({ api, threadId, target, phone, revision, writingStage = null, writingHabits = null, noted = null, onClose }: Props) {
  const [root, setRoot] = useState<string | null>(target.file?.root ?? null);
  const [dirs, setDirs] = useState<Map<string, FolderListing | 'loading' | 'error'>>(new Map());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [shown, setShown] = useState<Shown | null>(null);
  const [line, setLine] = useState<number | undefined>(target.line);
  const [wrap, setWrap] = useState(() => (localStorage.getItem(WRAP_KEY) ?? (phone ? '1' : '0')) === '1');
  const [treeOpen, setTreeOpen] = useState(() => localStorage.getItem(TREE_KEY) !== '0');
  const [source, setSource] = useState(false);
  const [width, setWidth] = useState(() => clampWidth(savedWidth()));
  const [query, setQuery] = useState('');
  const [found, setFound] = useState<{ query: string; root: string; paths: string[]; truncated: boolean } | null>(null);
  // Where the tree may stand, for the root switcher: fetched the first time it is asked for.
  const [rootList, setRootList] = useState<Array<{ path: string; kind: string }> | null>(null);
  const rootsRef = useRef<Promise<Array<{ path: string; kind: string }>> | null>(null);
  const loadRoots = useCallback(() => {
    rootsRef.current ??= api.threadRoots(threadId).then((r) => {
      setRootList(r.roots);
      return r.roots;
    }).catch(() => [] as Array<{ path: string; kind: string }>);
    return rootsRef.current;
  }, [api, threadId]);
  // On a phone: the file, or the folder being looked through.
  const [phoneFolder, setPhoneFolder] = useState<string | null>(target.path ? null : '');
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [confirmLeave, setConfirmLeave] = useState(false);
  const picture = useRef<string | null>(null);
  const fileGeneration = useRef(0);
  const savingRequest = useRef<number | null>(null);
  const editingRef = useRef(editing);
  editingRef.current = editing;
  useEffect(() => () => { fileGeneration.current++; }, []);
  /** Writes the unsaved text now, instead of when typing pauses. */
  const pendingDraft = useRef<(() => void) | null>(null);
  useEffect(() => () => pendingDraft.current?.(), []);

  // On a phone the page is a step in the browser's history: the back gesture closes it, not the hub.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const pushed = useRef(false);
  useEffect(() => {
    if (!phone) return;
    window.history.pushState({ ...window.history.state, rahFiles: true }, '');
    pushed.current = true;
    const onPop = () => {
      if (!pushed.current) return;
      pushed.current = false;
      closeRef.current();
    };
    window.addEventListener('popstate', onPop);
    return () => {
      window.removeEventListener('popstate', onPop);
      if (pushed.current) {
        pushed.current = false;
        window.history.back();
      }
    };
  }, [phone]);
  const close = () => (pushed.current ? window.history.back() : onClose());

  const loadDir = useCallback(
    async (path: string | null) => {
      const key = path ?? '';
      setDirs((m) => (m.get(key) && m.get(key) !== 'error' ? m : new Map(m).set(key, 'loading')));
      try {
        const listing = await api.threadTree(threadId, path ?? undefined);
        // The thread's folder is only the starting point: a link may already have moved the tree.
        if (!path) setRoot((r) => r ?? listing.root);
        setDirs((m) => {
          const next = new Map(m).set(listing.path, listing);
          if (!path) next.delete('');
          return next;
        });
        return listing;
      } catch {
        setDirs((m) => new Map(m).set(key, 'error'));
        return null;
      }
    },
    [api, threadId],
  );

  // The thread's folder first: the tree starts there, and it is where paths are counted from.
  useEffect(() => {
    void loadDir(null);
  }, [loadDir]);

  const releasePicture = () => {
    if (picture.current?.startsWith('blob:')) URL.revokeObjectURL(picture.current);
    picture.current = null;
  };
  useEffect(() => releasePicture, []);

  const openFile = useCallback(
    async (path: string, at?: number, preloaded?: TextFile) => {
      const generation = ++fileGeneration.current;
      savingRequest.current = null;
      setLine(at);
      setSource(false);
      pendingDraft.current?.();
      setEditing(null);
      setPhoneFolder(null);
      setShown({ state: 'loading', path });
      try {
        const file = preloaded ?? (await api.threadText(threadId, path));
        if (generation !== fileGeneration.current) return;
        setRoot((r) => r ?? file.root);
        releasePicture();
        if (IMAGE_FILE.test(file.path)) {
          try {
            const url = await pictureUrl(await api.threadFile(threadId, file.path));
            if (generation !== fileGeneration.current) {
              if (url.startsWith('blob:')) URL.revokeObjectURL(url);
              return;
            }
            picture.current = url;
            setShown({ state: 'picture', file, url });
            return;
          } catch {
            // Named like a picture but not one: shown as whatever it is.
          }
        }
        // A video is streamed from a signed link, so seeking works and the whole file is never
        // pulled into memory. The player fetches only what it needs.
        if (VIDEO_FILE.test(file.path)) {
          try {
            const link = await api.downloadLink(threadId, file.path, false);
            if (generation !== fileGeneration.current) return;
            setShown({ state: 'video', file, url: link.url });
            return;
          } catch {
            // No link: fall through to the binary notice, which still offers a download.
          }
        }
        // A report the browser reads in its own viewer, streamed the same way.
        if (PDF_FILE.test(file.path)) {
          try {
            const link = await api.downloadLink(threadId, file.path, false);
            if (generation !== fileGeneration.current) return;
            setShown({ state: 'pdf', file, url: link.url });
            return;
          } catch {
            // No link: the binary notice with its download button stands in.
          }
        }
        if (generation !== fileGeneration.current) return;
        setShown({ state: 'text', file });
        // The body takes the page: well over half the window with the tree folded away, until the
        // person has dragged a width of their own. Any other file goes back to the panel's share.
        const asArticle = isArticleFile(file) || (writingHabits !== null && file.path === writingHabits);
        if (!localStorage.getItem(WIDTH_KEY)) setWidth(clampWidth(asArticle ? Math.max(640, Math.round(window.innerWidth * 0.55)) : Math.round(window.innerWidth * 0.45)));
        if (asArticle) setTreeOpen(localStorage.getItem(TREE_KEY) === '1');
        // Words left unsaved here before, kept on this device: back in the editor, as they were.
        const draft = file.editable ? readBodyDraft(localStorage, threadId, file.path) : null;
        if (draft) setEditing({ text: draft.text, baseHash: draft.baseHash, saving: false, conflict: draft.baseHash === file.hash ? null : file.hash ?? null, error: null });
      } catch {
        if (generation === fileGeneration.current) setShown({ state: 'error', path, message: t('files.notFound') });
      }
    },
    [api, threadId, writingHabits],
  );

  // Each time something is opened from outside: a file, a folder, or the thread's folder alone.
  useEffect(() => {
    if (target.path) void openFile(target.path, target.line, target.file);
    else if (target.folder) void revealFolder(target.folder);
    else setPhoneFolder('');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target.seq]);

  // The tree opens down to the file on show.
  const filePath = shown && shown.state !== 'loading' && shown.state !== 'error' ? shown.file.path : null;
  useEffect(() => {
    if (!root || !filePath) return;
    const folders = foldersTo(root, filePath);
    if (folders.length === 0 || folders[0] !== root) return;
    setExpanded((s) => new Set([...s, ...folders]));
    for (const folder of folders) if (!dirs.has(folder)) void loadDir(folder);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [root, filePath]);

  // The filter looks through the root the tree stands on, not just what it has opened.
  useEffect(() => {
    const q = query.trim();
    if (!q) return setFound(null);
    let alive = true;
    const timer = setTimeout(() => {
      api
        .findFiles(threadId, q, root ?? undefined)
        .then((r) => alive && setFound({ query: q, root: r.root, paths: r.paths, truncated: r.truncated }))
        .catch(() => alive && setFound({ query: q, root: root ?? '', paths: [], truncated: false }));
    }, 180);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [api, threadId, query, root]);

  const toggleFolder = (path: string) => {
    setExpanded((s) => {
      const next = new Set(s);
      if (next.has(path)) next.delete(path);
      else {
        next.add(path);
        if (!dirs.has(path) || dirs.get(path) === 'error') void loadDir(path);
      }
      return next;
    });
  };

  const showFolder = (path: string) => {
    if (phone) {
      setPhoneFolder(path);
      if (!dirs.has(path)) void loadDir(path);
      return;
    }
    setTreeOpen(true);
    if (root) for (const folder of [...foldersTo(root, path), path]) if (!dirs.has(folder)) void loadDir(folder);
    setExpanded((s) => new Set([...s, ...(root ? foldersTo(root, path) : []), path]));
  };

  /** Stands the tree on another root: the thread's folder, a project directory, or the home directory. */
  const switchRoot = (path: string) => {
    if (path === root) return;
    setRoot(path);
    setExpanded(new Set());
    setQuery('');
    void loadDir(path);
    if (phone) setPhoneFolder(path);
  };

  /**
   * A folder named from outside, as an answer's link does: stand the tree on the root that holds
   * it (or the folder itself when no root does), then open the tree down to it.
   */
  const revealFolder = useCallback(
    async (path: string) => {
      let base = root;
      if (!base || !(path === base || path.startsWith(`${base}/`))) {
        const roots = await loadRoots();
        base = roots.find((r) => path === r.path || path.startsWith(`${r.path}/`))?.path ?? null;
      }
      const stand = base ?? path;
      if (stand !== root) {
        setRoot(stand);
        setExpanded(new Set());
      }
      if (phone) setPhoneFolder(path);
      else setTreeOpen(true);
      const steps = [stand, ...foldersTo(stand, path).slice(1), path];
      for (const folder of steps) if (!dirs.has(folder)) void loadDir(folder);
      setExpanded((s) => new Set([...s, ...steps]));
    },
    [root, phone, loadRoots, loadDir, dirs],
  );

  const copyPath = async () => {
    if (!filePath) return;
    setCopied(await copyText(filePath));
    setTimeout(() => setCopied(false), 1400);
  };

  const remember = (key: string, value: boolean) => localStorage.setItem(key, value ? '1' : '0');

  // Dragging the left edge sets the width; it is kept on this device.
  const startResize = (e: React.PointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = width;
    const move = (ev: PointerEvent) => setWidth(clampWidth(startW + startX - ev.clientX));
    const up = (ev: PointerEvent) => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      localStorage.setItem(WIDTH_KEY, String(clampWidth(startW + startX - ev.clientX)));
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };
  useEffect(() => {
    const onResize = () => setWidth((w) => clampWidth(w));
    onResize();
    window.addEventListener('resize', onResize);
    // The sidebar folding away or back changes what there is to take, at the same window size.
    const sidebar = document.querySelector('.app > .sidebar');
    const observer = sidebar && typeof ResizeObserver !== 'undefined' ? new ResizeObserver(onResize) : null;
    if (observer && sidebar) observer.observe(sidebar);
    return () => {
      window.removeEventListener('resize', onResize);
      observer?.disconnect();
    };
  }, []);

  const textFile = shown?.state === 'text' ? shown.file : null;
  const canEdit = !!textFile?.editable && typeof textFile.hash === 'string' && textFile.text !== null;
  const dirty = !!editing && !!textFile && (editing.text !== textFile.text || editing.baseHash !== textFile.hash);
  // Notes carry the path the agent can find the file by: relative to the thread's folder.
  const noteFile = textFile && root && textFile.path.startsWith(`${root}/`) ? textFile.path.slice(root.length + 1) : textFile?.path;

  // Unsaved text is kept on this device a moment after typing stops, and whenever the page or the
  // file goes away.
  useEffect(() => {
    if (!editing || !textFile) return;
    const path = textFile.path;
    const keep = dirty ? { text: editing.text, baseHash: editing.baseHash } : null;
    let flushed = false;
    const flush = () => {
      if (flushed) return;
      flushed = true;
      pendingDraft.current = null;
      writeBodyDraft(localStorage, threadId, path, keep);
    };
    pendingDraft.current = flush;
    const timer = setTimeout(flush, 400);
    window.addEventListener('pagehide', flush);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('pagehide', flush);
    };
  }, [editing?.text, editing?.baseHash, dirty, textFile, threadId]);

  useEffect(() => {
    if (!textFile?.editable || savingRequest.current !== null) return;
    const generation = fileGeneration.current;
    let alive = true;
    void api.threadText(threadId, textFile.path).then(file => {
      if (!alive || generation !== fileGeneration.current || savingRequest.current !== null) return;
      const draft = editingRef.current;
      if (draft) {
        if (file.hash !== draft.baseHash && file.hash) setEditing(e => e && { ...e, conflict: file.hash ?? null });
      } else setShown({ state: 'text', file });
    }).catch(() => undefined);
    return () => { alive = false; };
    // Only thread updates trigger a reread, never typing or our own file state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revision]);

  const save = async (baseHash = editing?.baseHash) => {
    if (!editing || !textFile || baseHash === undefined || savingRequest.current === fileGeneration.current) return;
    const generation = fileGeneration.current;
    savingRequest.current = generation;
    const text = editing.text;
    setEditing((e) => e && { ...e, saving: true, error: null });
    try {
      const { hash } = await api.saveWritingText(threadId, textFile.path, text, baseHash);
      if (generation !== fileGeneration.current) {
        const draft = readBodyDraft(localStorage, threadId, textFile.path);
        if (draft?.baseHash === baseHash) writeBodyDraft(localStorage, threadId, textFile.path, draft.text === text ? null : { text: draft.text, baseHash: hash });
        return;
      }
      pendingDraft.current?.();
      const latest = editingRef.current;
      writeBodyDraft(localStorage, threadId, textFile.path, latest && latest.text !== text ? { text: latest.text, baseHash: hash } : null);
      setShown((s) => (s?.state === 'text' && s.file.path === textFile.path ? { state: 'text', file: { ...s.file, text, hash, size: new Blob([text]).size } } : s));
      setEditing((e) => e && { ...e, baseHash: hash, saving: false, conflict: null });
    } catch (error) {
      if (generation !== fileGeneration.current) return;
      const onDisk = conflictHash(error);
      setEditing((e) => e && { ...e, saving: false, conflict: onDisk, error: onDisk ? null : error instanceof Error ? error.message : String(error) });
    } finally {
      if (savingRequest.current === generation) savingRequest.current = null;
    }
  };

  // The version on disk replaces what was typed; the banner above said to copy it first.
  const loadLatest = async () => {
    if (!textFile) return;
    const generation = fileGeneration.current;
    const requestedText = editingRef.current?.text;
    try {
      const file = await api.threadText(threadId, textFile.path);
      if (generation !== fileGeneration.current) return;
      // Typing continued while the replacement was loading; leave those new words intact.
      if (editingRef.current?.text !== requestedText) {
        setEditing(e => e && { ...e, conflict: file.hash ?? null });
        return;
      }
      pendingDraft.current?.();
      writeBodyDraft(localStorage, threadId, textFile.path, null);
      setShown({ state: 'text', file });
      setEditing(file.editable && typeof file.hash === 'string' && file.text !== null ? { text: file.text, baseHash: file.hash, saving: false, conflict: null, error: null } : null);
    } catch (error) {
      if (generation !== fileGeneration.current) return;
      setEditing((e) => e && { ...e, error: error instanceof Error ? error.message : String(error) });
    }
  };

  const startEditing = () => {
    if (editing || !canEdit || !textFile) return;
    setSource(false);
    setEditing({ text: textFile.text ?? '', baseHash: textFile.hash!, saving: false, conflict: null, error: null });
  };
  const stopEditing = (discard = false) => {
    if (dirty && !discard) return setConfirmLeave(true);
    pendingDraft.current?.();
    if (textFile) writeBodyDraft(localStorage, threadId, textFile.path, null);
    pendingDraft.current = null;
    setConfirmLeave(false);
    setEditing(null);
  };

  // Ctrl/Cmd+S saves wherever the focus is while the body is open for editing.
  const saveRef = useRef(save);
  saveRef.current = save;
  useEffect(() => {
    if (!editing) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === 's') {
        e.preventDefault();
        void saveRef.current();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [!!editing]);

  const crumbs = root ? crumbsOf(root, phone && phoneFolder !== null ? phoneFolder || root : (filePath ?? root)) : [];
  const markdown = shown?.state === 'text' && shown.file.text !== null && languageOf(shown.file.path) === 'markdown';
  // The body reads as an article; so does the habits note, which is not this thread's draft and so
  // stands under no stage.
  const bodyArticle = !!markdown && shown?.state === 'text' && isArticleFile(shown.file);
  const article = bodyArticle || (!!markdown && shown?.state === 'text' && writingHabits !== null && shown.file.path === writingHabits);
  const articleTitle = article
    ? t('writing.articleMeta', bodyArticle && writingStage ? stageLabel(writingStage) : null, countWords(shown.file.text ?? '').toLocaleString())
    : null;
  const svg = shown?.state === 'picture' && /\.svg$/i.test(shown.file.path);

  // React treats each renderer as a component type. Stable types keep diagrams and pictures
  // mounted while unrelated panel state changes, including a theme switch.
  const markdownComponents = useMemo<Components>(() => ({
    // The title says where the piece stands: the stage, and how much is written. The key must be
    // absent outside the article, not `undefined`: an own property with that value is taken as
    // the component itself, and any heading would bring the whole page down.
    ...(article && {
      h1: ({ children }: { children?: ReactNode }) => (
        <>
          <h1>{children}</h1>
          <div className="article-meta">{articleTitle}</div>
        </>
      ),
    }),
    a: ({ href, children }) => {
      if (href && /^https?:\/\//i.test(href))
        return (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        );
      const to = href ? resolveRelative(parentDir(filePath ?? ''), href) : null;
      if (!to) return <>{children}</>;
      return (
        <a className="file-link" role="button" tabIndex={0} onClick={() => void openFile(to)}>
          {children}
        </a>
      );
    },
    img: ({ src, alt }) => {
      if (src && /^https?:/i.test(src)) return <img src={src} alt={alt ?? ''} loading="lazy" />;
      const path = relativePicture(filePath ?? '', src);
      return path ? <FilePicture api={api} threadId={threadId} path={path} alt={alt ?? ''} onOpen={() => void openFile(path)} /> : null;
    },
    pre: ({ children }) => {
      const code = Children.toArray(children).find((c) => isValidElement<{ className?: string; children?: ReactNode }>(c));
      const props = isValidElement<{ className?: string; children?: ReactNode }>(code) ? code.props : null;
      if (props && /\blanguage-mermaid\b/.test(props.className ?? '')) return <MermaidDiagram code={String(props.children ?? '').replace(/\n$/, '')} />;
      return <pre>{children}</pre>;
    },
  }), [api, threadId, openFile, filePath, article, articleTitle]);

  const filter = (
    <label className="files-filter">
      <IconSearch size={14} />
      <input value={query} placeholder={t('files.filterPlaceholder')} aria-label={t('files.filter')} spellCheck={false} autoCapitalize="off" onChange={(e) => setQuery(e.target.value)} />
      {query && (
        <button type="button" className="files-filter-clear" aria-label={t('files.clearFilter')} onClick={() => setQuery('')}>
          <IconClose size={12} />
        </button>
      )}
    </label>
  );

  /** Says where the tree stands — the thread's folder, a project directory, the home directory —
   * and offers the choice; the list itself is read only when the menu is opened. */
  const rootsMenu = (
    <Popover
      label={t('files.roots')}
      triggerLabel={t('files.roots')}
      triggerTitle={root ?? t('files.roots')}
      triggerClassName="files-roots"
      placement="below"
      align="start"
      onOpen={loadRoots}
      trigger={
        <>
          <IconFolders size={14} />
          <span className="files-roots-name">{root ? folderName(root) : t('common.loading')}</span>
          <IconChevron size={12} open />
        </>
      }
    >
      {close => <>
        {rootList === null && <div className="files-note files-roots-note">{t('common.loading')}</div>}
        {rootList?.map((r) => (
          <button
            key={r.path}
            type="button"
            role="menuitemradio"
            aria-checked={r.path === root}
            className={`pop-item files-roots-row${r.path === root ? ' on' : ''}`}
            onClick={() => {
              close();
              switchRoot(r.path);
            }}
          >
            <IconFolder size={15} />
            <span className="files-roots-text">
              <span className="files-roots-text-name">{folderName(r.path)}</span>
              <span className="files-roots-text-kind">{rootKindLabel(r.kind)}</span>
            </span>
          </button>
        ))}
      </>}
    </Popover>
  );

  const results = found && (
    <div className="files-results" role="list">
      {found.paths.map((rel) => {
        const path = `${found.root}/${rel}`;
        const slash = rel.lastIndexOf('/');
        return (
          <button
            key={rel}
            type="button"
            role="listitem"
            className={`files-row${path === filePath ? ' current' : ''}`}
            title={rel}
            onClick={() => {
              setQuery('');
              void openFile(path);
            }}
          >
            <IconFile size={14} />
            <span className="files-name">{rel.slice(slash + 1)}</span>
            {slash > 0 && <span className="files-hint">{rel.slice(0, slash)}</span>}
          </button>
        );
      })}
      {found.paths.length === 0 && <div className="files-note">{t('files.noMatch', found.query)}</div>}
      {found.truncated && <div className="files-note">{t('files.truncatedResults', found.paths.length)}</div>}
    </div>
  );

  const treeRows = (path: string, depth: number): ReactNode => {
    const listing = dirs.get(path);
    if (!listing || listing === 'loading') return <div className="files-note" style={{ paddingLeft: 12 + depth * 14 }}>{t('common.loading')}</div>;
    if (listing === 'error') return <div className="files-note" style={{ paddingLeft: 12 + depth * 14 }}>{t('files.folderFailed')}</div>;
    return (
      <>
        {listing.entries.map((entry) => {
          const full = `${listing.path}/${entry.name}`;
          // On a phone a folder is a page of its own: tapping one goes into it.
          const open = !phone && expanded.has(full);
          return (
            <div key={entry.name} role="treeitem" aria-expanded={entry.type === 'dir' ? open : undefined}>
              <EntryRow
                entry={entry}
                depth={depth}
                open={open}
                current={full === filePath}
                onClick={() => (entry.type === 'dir' ? (phone ? showFolder(full) : toggleFolder(full)) : void openFile(full))}
              />
              {entry.type === 'dir' && open && treeRows(full, depth + 1)}
            </div>
          );
        })}
        {listing.entries.length === 0 && <div className="files-note" style={{ paddingLeft: 12 + depth * 14 }}>{t('files.emptyFolder')}</div>}
        {listing.truncated && <div className="files-note" style={{ paddingLeft: 12 + depth * 14 }}>{t('files.tooMany')}</div>}
      </>
    );
  };

  const editor = editing && textFile && (
    <div className="files-viewer files-editing">
      {editing.conflict && (
        <div className="writing-conflict" role="alert">
          <span className="writing-conflict-text">{t('writing.conflict')}</span>
          <span className="writing-conflict-actions">
            <button type="button" className="btn-secondary" disabled={editing.saving} onClick={() => void loadLatest()}>{t('writing.loadLatest')}</button>
            <button type="button" className="btn-secondary" disabled={editing.saving} onClick={() => void save(editing.conflict!)}>{t('writing.overwrite')}</button>
          </span>
        </div>
      )}
      {editing.error && <div className="writing-conflict" role="alert"><span className="writing-conflict-text">{t('writing.saveFailed', editing.error)}</span></div>}
      <WritingPaper text={editing.text} path={noteFile!} autoFocus={!phone} onChange={(text) => setEditing((v) => v && { ...v, text })} />
    </div>
  );

  // Reading and writing are the same sheet of paper; this pair of buttons turns it over.
  const readWrite = canEdit && textFile && (
    <div className="files-seg" role="group" aria-label={t('writing.readWrite')}>
      <button type="button" className={editing ? '' : 'on'} aria-pressed={!editing} onClick={() => editing && stopEditing()}>{t('writing.read')}</button>
      <button type="button" className={editing ? 'on' : ''} aria-pressed={!!editing} onClick={startEditing}>{t('writing.write')}</button>
    </div>
  );
  const savedState = canEdit && textFile && (
    <span className={`writing-state${editing && dirty ? ' dirty' : ''}`} role="status">
      {editing && dirty ? t('writing.unsaved') : t('writing.saved')}
    </span>
  );

  const editActions = editing && (
    <>
      {savedState}
      {/* On a phone the back button is the way back to reading; the pair would only crowd it. */}
      {!phone && readWrite}
      <button type="button" className="files-text-btn primary-btn" title={t('writing.saveHint')} disabled={editing.saving || (!dirty && !editing.conflict)} onClick={() => void save()}>
        {editing.saving ? t('writing.saving') : t('writing.save')}
      </button>
    </>
  );

  const leaveDialog = confirmLeave && (
    <Dialog title={t('writing.discardTitle')} message={t('writing.discardMessage')} confirmLabel={t('writing.discard')} danger
      onCancel={() => setConfirmLeave(false)} onConfirm={() => stopEditing(true)} />
  );

  const viewer = editor || (
    <div className="files-viewer" data-note-file={noteFile ?? undefined} data-writing-body={bodyArticle || undefined}>
      {!shown && (
        <div className="files-empty">
          <IconFile size={30} />
          <span>{phone ? '' : t('files.pick')}</span>
        </div>
      )}
      {shown?.state === 'loading' && <div className="files-empty">{t('common.loading')}</div>}
      {shown?.state === 'error' && <div className="files-empty">{shown.message}</div>}
      {shown?.state === 'picture' && (svg && source ? <SvgSource file={shown.file} wrap={wrap} /> : <div className="files-picture"><img src={shown.url} alt={shown.file.path} /></div>)}
      {shown?.state === 'video' && (
        <div className="files-video">
          <video src={shown.url} controls playsInline preload="metadata" />
        </div>
      )}
      {shown?.state === 'pdf' && (
        <div className="files-pdf">
          <iframe src={shown.url} title={shown.file.path} loading="lazy" />
        </div>
      )}
      {shown?.state === 'text' && shown.file.text === null && (
        <div className="files-empty">
          <IconFile size={30} />
          <span>{t('files.binary', sizeLabel(shown.file.size))}</span>
          <DownloadButton api={api} threadId={threadId} path={shown.file.path} label={t('files.download')} />
        </div>
      )}
      {shown?.state === 'text' && shown.file.text !== null && markdown && !source && (
        <div className={`files-markdown markdown${article ? ' article' : ''}`}>
          <Markdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={article ? [rehypeArticleMarks] : []}
            components={markdownComponents}
          >
            {shown.file.text}
          </Markdown>
        </div>
      )}
      {shown?.state === 'text' && shown.file.text !== null && (!markdown || source) && <Code key={`${shown.file.path}:${target.seq}`} file={shown.file} line={line} wrap={wrap} />}
      {shown?.state === 'text' && shown.file.truncated && <div className="files-truncated">{t('files.truncated', sizeLabel((shown.file.text ?? '').length))}</div>}
    </div>
  );

  const secondaryActions = filePath && (
    <>
      {(markdown || svg) && (
        <button type="button" className={phone ? 'pop-item' : `icon-btn files-toggle${source ? ' on' : ''}`} role={phone ? 'menuitem' : undefined} aria-pressed={source} title={source ? t('files.showRendered') : t('files.showSource')} onClick={() => setSource((v) => !v)}>
          <span className="files-toggle-text">{source ? t('files.preview') : t('files.source')}</span>
        </button>
      )}
      {((shown?.state === 'text' && (!markdown || source)) || (svg && source)) && (
        <button
          type="button"
          className={phone ? 'pop-item' : `icon-btn${wrap ? ' on' : ''}`}
          role={phone ? 'menuitemcheckbox' : undefined}
          aria-checked={phone ? wrap : undefined}
          aria-pressed={wrap}
          aria-label={t('files.wrap')}
          title={t('files.wrap')}
          onClick={() => {
            setWrap(!wrap);
            remember(WRAP_KEY, !wrap);
          }}
        >
          <IconWrap size={16} />{phone && t('files.wrap')}
        </button>
      )}
      <button type="button" className={phone ? 'pop-item' : 'icon-btn'} role={phone ? 'menuitem' : undefined} aria-label={t('files.copyPath')} title={copied ? t('common.copied') : t('files.copyPath')} onClick={() => void copyPath()}>
        <IconCopy size={16} />{phone && (copied ? t('common.copied') : t('files.copyPath'))}
      </button>
      <DownloadButton api={api} threadId={threadId} path={filePath} label={t('files.download')} icon={!phone} menu={phone} />
    </>
  );
  const fileActions = filePath && (editActions || <>{savedState}{readWrite}{secondaryActions}</>);

  if (phone) {
    const inFolder = phoneFolder !== null;
    const folder = inFolder ? phoneFolder || root : null;
    const name = inFolder ? crumbs[crumbs.length - 1]?.name ?? t('files.files') : (filePath ?? (shown && 'path' in shown ? shown.path : '')).split('/').pop();
    return (
      <div className={`files-panel phone${article ? ' article' : ''}`} role="dialog" aria-label={t('files.files')}>
        <div className="files-head">
          <button
            type="button"
            className="icon-btn"
            aria-label={inFolder && shown ? t('files.backToFile') : editing ? t('writing.read') : t('common.close')}
            onClick={() => (inFolder && shown ? setPhoneFolder(null) : editing ? stopEditing() : close())}
          >
            <span className="files-back">
              <IconChevron size={18} />
            </span>
          </button>
          <div className="files-title">
            <div className="files-title-name" title={name}>{article ? name?.replace(/\.md$/i, '') : name}</div>
            {crumbs.length > 1 && inFolder && (
              <div className="files-title-sub">
                {crumbs.slice(0, -1).map((c, i) => (
                  <button key={c.path} type="button" onClick={() => showFolder(c.path)}>
                    {i > 0 && <span className="files-sep">/</span>}
                    {c.name}
                  </button>
                ))}
              </div>
            )}
          </div>
          {!inFolder && (editing ? editActions : <>
            {readWrite}
            <Popover label={t('files.more')} triggerLabel={t('files.more')} triggerClassName="icon-btn" trigger={<IconMore size={18} />} placement="below" align="end">
              {closeMenu => <>
                <div className="files-menu-path">{filePath}</div>
                <div className="files-menu-actions">{secondaryActions}</div>
                <button type="button" role="menuitem" className="pop-item" onClick={() => { closeMenu(); showFolder(filePath && root && filePath.startsWith(`${root}/`) ? parentDir(filePath) : (root ?? '')); }}>
                  <IconFolder size={17} />{t('files.browse')}
                </button>
              </>}
            </Popover>
          </>)}
        </div>
        {inFolder ? (
          <div className="files-list">
            <div className="files-toolbar">
              {rootsMenu}
              {filter}
            </div>
            {results ?? (folder ? <div role="tree">{treeRows(folder, 0)}</div> : <div className="files-note">{t('common.loading')}</div>)}
          </div>
        ) : (
          viewer
        )}
        {/* The box these notes wait in is hidden behind this full-screen page; say they are there. */}
        {noted && noted.count > 0 && (
          <div key={noted.nonce} className="files-noted" role="status">
            <span className="files-noted-text">{t('files.noted', noted.count)}</span>
            <button type="button" className="files-noted-back" onClick={close}>{t('files.backToChat')}</button>
          </div>
        )}
        {leaveDialog}
      </div>
    );
  }

  return (
    <aside className={`files-panel${article ? ' article' : ''}`} style={{ width }} aria-label={t('files.files')}>
      <div className="files-resize" onPointerDown={startResize} role="separator" aria-orientation="vertical" aria-label={t('common.resize')} />
      <div className="files-head">
        <nav className="files-crumbs" aria-label={t('files.path')}>
          {crumbs.map((c, i) => {
            const last = i === crumbs.length - 1;
            return (
              <span key={c.path} className="files-crumb">
                {i > 0 && <IconChevron size={12} />}
                {last && filePath ? (
                  <span className="files-crumb-current" title={c.path}>
                    {c.name}
                  </span>
                ) : (
                  <button type="button" title={c.path} onClick={() => showFolder(c.path)}>
                    {c.name}
                  </button>
                )}
              </span>
            );
          })}
        </nav>
        <div className="files-actions">
          {fileActions}
          <button
            type="button"
            className={`icon-btn${treeOpen ? ' on' : ''}`}
            aria-pressed={treeOpen}
            aria-label={t('files.tree')}
            title={t('files.tree')}
            onClick={() => {
              setTreeOpen(!treeOpen);
              remember(TREE_KEY, !treeOpen);
            }}
          >
            <IconSidebar size={16} />
          </button>
          <button type="button" className="icon-btn" aria-label={t('files.close')} title={t('common.close')} onClick={onClose}>
            <IconClose size={16} />
          </button>
        </div>
      </div>
      {/* Nothing open: the folder is the content, not a dead pane beside an empty viewer. */}
      <div className={`files-body${shown === null && !editing && treeOpen ? ' only-tree' : ''}`}>
        {!(shown === null && !editing && treeOpen) && viewer}
        {treeOpen && (
          <div className="files-tree">
            <div className="files-toolbar">
              {rootsMenu}
              {filter}
            </div>
            <div className="files-tree-scroll">{results ?? (root ? <div role="tree">{treeRows(root, 0)}</div> : <div className="files-note">{t('common.loading')}</div>)}</div>
          </div>
        )}
      </div>
      {leaveDialog}
    </aside>
  );
}

/**
 * The body as one page the person writes on: a transparent-text textarea over a layer that
 * colours the Markdown's own marks faint. Both share the article's every measure — font, size,
 * leading, width, padding — so the page is the same sheet of paper as the preview, and the caret,
 * wrapped lines and scrolling line up with what is seen. An input method composing shows its own
 * text for a moment (the page turns plain) rather than typing invisible words.
 */
function WritingPaper({ text, path, autoFocus, onChange }: { text: string; path: string; autoFocus?: boolean; onChange: (text: string) => void }) {
  const layer = useRef<HTMLDivElement>(null);
  const marked = useMemo(() => highlightWritingSource(text), [text]);
  return (
    <div className="writing-paper" data-writing-body>
      <div className="writing-page">
        <div ref={layer} className="writing-paper-layer" aria-hidden="true" dangerouslySetInnerHTML={{ __html: marked }} />
        <textarea
          className="writing-paper-input"
          data-note-file={path}
          aria-label={t('writing.body')}
          value={text}
          autoFocus={autoFocus}
          spellCheck={false}
          onChange={(e) => onChange(e.target.value)}
          onScroll={(e) => {
            if (!layer.current) return;
            layer.current.scrollTop = e.currentTarget.scrollTop;
            layer.current.scrollLeft = e.currentTarget.scrollLeft;
          }}
          onCompositionStart={(e) => e.currentTarget.classList.add('composing')}
          onCompositionEnd={(e) => e.currentTarget.classList.remove('composing')}
          onKeyDown={(e: KeyboardEvent<HTMLTextAreaElement>) => {
            // A tab is a character in the text, not a jump out of the page.
            if (e.key === 'Tab' && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
              e.preventDefault();
              const el = e.currentTarget;
              const { selectionStart: a, selectionEnd: b } = el;
              onChange(`${el.value.slice(0, a)}\t${el.value.slice(b)}`);
              requestAnimationFrame(() => el.setSelectionRange(a + 1, a + 1));
            }
          }}
        />
      </div>
    </div>
  );
}

function EntryRow({ entry, depth, open, current, onClick }: { entry: FolderEntry; depth: number; open: boolean; current: boolean; onClick: () => void }) {
  const dir = entry.type === 'dir';
  return (
    <button
      type="button"
      className={`files-row${current ? ' current' : ''}${entry.ignored ? ' ignored' : ''}`}
      style={{ paddingLeft: 6 + depth * 14 }}
      title={dir ? undefined : entry.size !== undefined ? `${entry.name} · ${sizeLabel(entry.size)}` : entry.name}
      onClick={onClick}
    >
      <span className="files-caret">{dir && <IconChevron size={11} open={open} />}</span>
      {dir ? <IconFolder size={14} /> : <IconFile size={14} />}
      <span className="files-name">{entry.name}</span>
      {!dir && entry.size !== undefined && <span className="files-size">{sizeLabel(entry.size)}</span>}
    </button>
  );
}

/**
 * Saves one of the thread's files: a signed link is minted, then the browser saves what it names.
 * In the phone menu the link is minted as the menu opens and the item is a real link: a click that
 * waits for the network before saving loses its standing as a tap, and an installed iPhone app
 * then drops the download without a word.
 */
function DownloadButton({ api, threadId, path, label, icon, menu }: { api: Api; threadId: string; path: string; label: string; icon?: boolean; menu?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [ready, setReady] = useState<{ url: string; name: string; size: number } | null>(null);
  const [started, setStarted] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!menu) return;
    let live = true;
    setReady(null);
    setStarted(false);
    setFailed(false);
    api.downloadLink(threadId, path, true).then(
      (link) => live && setReady({ url: link.url, name: link.name, size: link.size }),
      () => live && setFailed(true),
    );
    return () => { live = false; };
  }, [api, threadId, path, menu, attempt]);
  const start = async () => {
    setBusy(true);
    setFailed(false);
    try {
      const link = await api.downloadLink(threadId, path, true);
      saveAs(link.url, link.name);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };
  const title = failed ? t('files.downloadFailed') : label;
  if (icon)
    return (
      <button type="button" className="icon-btn" aria-label={label} title={title} disabled={busy} onClick={() => void start()}>
        <IconDownload size={16} />
      </button>
    );
  if (menu && ready)
    return (
      <a className="pop-item" role="menuitem" href={ready.url} download={ready.name} onClick={() => setStarted(true)}>
        <IconDownload size={15} />
        <span>{started ? t('files.downloadStarted', sizeLabel(ready.size)) : label}</span>
      </a>
    );
  if (menu)
    return (
      <button type="button" className="pop-item" role="menuitem" aria-disabled={!failed} onClick={() => failed && setAttempt((n) => n + 1)}>
        <IconDownload size={15} />
        <span>{failed ? title : t('files.downloading')}</span>
      </button>
    );
  return (
    <button type="button" className="btn-secondary files-download" disabled={busy} onClick={() => void start()}>
      <IconDownload size={15} />
      <span>{busy ? t('files.downloading') : title}</span>
    </button>
  );
}

/** What a root is, as the switcher says it: the thread's own folder, the agent's session folder, a project directory, the home directory. */
function rootKindLabel(kind: string): string {
  switch (kind) {
    case 'thread': return t('files.rootThread');
    case 'session': return t('files.rootSession');
    case 'project': return t('files.rootProject');
    case 'home': return t('files.rootHome');
    default: return kind;
  }
}

/** Rows of code with their numbers; the line a link pointed at is marked and scrolled to. */
function Code({ file, line, wrap }: { file: TextFile; line?: number; wrap: boolean }) {
  const text = file.text ?? '';
  const language = languageOf(file.path);
  const [html, setHtml] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!language) return;
    let alive = true;
    void loadHighlighter().then((m) => alive && setHtml(m.highlight(text, language)));
    return () => {
      alive = false;
    };
  }, [text, language]);

  const lines = useMemo(() => {
    const out = html !== null ? splitHighlighted(html) : text.split('\n').map(escapeHtml);
    // A file that ends with a newline has no empty last line to show.
    if (out.length > 1 && out[out.length - 1] === '') out.pop();
    return out;
  }, [html, text]);

  useLayoutEffect(() => {
    if (!line) return;
    const row = ref.current?.querySelector(`[data-line="${line}"]`);
    row?.scrollIntoView({ block: 'center' });
  }, [line, lines.length]);

  // The numbers' column is as wide as the largest number.
  const digits = String(lines.length).length;
  return (
    <div ref={ref} className={`files-code${wrap ? ' wrap' : ''}`} style={{ ['--digits' as string]: `${digits}ch` }}>
      {chunks(lines).map((chunk, c) => (
        <div key={c} className="files-chunk">
          {chunk.map((code, i) => {
            const n = c * CHUNK + i + 1;
            return (
              <div key={n} data-line={n} className={`files-line${n === line ? ' hit' : ''}`}>
                <span className="files-num">{n}</span>
                <span className="files-src" dangerouslySetInnerHTML={{ __html: code || ' ' }} />
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

/** Lines are drawn in blocks the browser can skip while they are off screen: a long file stays quick. */
const CHUNK = 200;
function chunks<T>(list: T[]): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < list.length; i += CHUNK) out.push(list.slice(i, i + CHUNK));
  return out;
}

/** An SVG's own text, for when the drawing is not what is wanted. */
function SvgSource({ file, wrap }: { file: TextFile; wrap: boolean }) {
  return file.text === null ? <div className="files-empty">{t('files.noSource')}</div> : <Code file={file} wrap={wrap} />;
}

/** A ```mermaid block in a Markdown file, drawn; the code stands in until then, or if it cannot be parsed. */
function MermaidDiagram({ code }: { code: string }) {
  const [diagram, setDiagram] = useState<string | null>(null);
  const [enlarged, setEnlarged] = useState(false);
  const large = useMemo(() => (enlarged && diagram ? enlargeMermaidDiagram(diagram) : null), [enlarged, diagram]);
  const closeLarge = useCallback(() => setEnlarged(false), []);
  useEffect(() => {
    let alive = true;
    let version = 0;
    const draw = () => {
      const request = ++version;
      void renderMermaidDiagram(code).then((svg) => alive && request === version && setDiagram(svg)).catch(() => {
        if (alive && request === version) setDiagram(null);
      });
    };
    draw();
    // Redrawn with the look when it changes while the file is on screen.
    const observer = new MutationObserver(draw);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-palette'] });
    return () => {
      alive = false;
      observer.disconnect();
    };
  }, [code]);
  if (diagram === null) return <pre><code className="language-mermaid">{code}</code></pre>;
  return (
    <>
      <div
        className="files-diagram"
        role="button"
        tabIndex={0}
        aria-label={t('view.enlarge')}
        title={t('view.enlarge')}
        onClick={(event) => {
          if (window.getSelection()?.isCollapsed === false || (event.target as Element).closest('a')) return;
          setEnlarged(true);
        }}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget || (event.key !== 'Enter' && event.key !== ' ')) return;
          event.preventDefault();
          setEnlarged(true);
        }}
        dangerouslySetInnerHTML={{ __html: diagram }}
      />
      {large && <Lightbox svg={large} onClose={closeLarge} />}
    </>
  );
}

/** A picture a Markdown file names by a relative path, fetched through the thread's image route. */
function FilePicture({ api, threadId, path, alt, onOpen }: { api: Api; threadId: string; path: string; alt: string; onOpen: () => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    let made: string | null = null;
    setUrl(null);
    setFailed(false);
    api
      .threadFile(threadId, path)
      .then(pictureUrl)
      .then((u) => {
        made = u;
        if (alive) setUrl(u);
        else if (u.startsWith('blob:')) URL.revokeObjectURL(u);
      })
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
      if (made?.startsWith('blob:')) URL.revokeObjectURL(made);
    };
  }, [api, threadId, path]);
  if (failed) return <span className="files-picture-missing" title={path}>{alt || path.split('/').pop()}</span>;
  if (!url) return <span className="files-picture-loading" aria-label={alt} />;
  return (
    <button type="button" className="files-inline-picture" title={path} onClick={onOpen}>
      <img src={url} alt={alt} />
    </button>
  );
}
