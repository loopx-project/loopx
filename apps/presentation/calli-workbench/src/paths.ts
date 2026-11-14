import { t } from './i18n';
/** Last path segment, for showing a project name without the full path. */
export function folderName(path: string): string {
  const parts = path.split('/').filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

const withSlash = (root: string) => (root.endsWith('/') ? root : `${root}/`);

/**
 * A path as shown in the UI: relative to the thread's directory when it is inside it.
 * `roots` holds every spelling of that directory. Agents often report the resolved path,
 * which differs from the one the thread was created with when a symlink is involved.
 */
export function relativePath(path: string, roots: string[]): string {
  for (const root of roots) {
    if (!root || root === '/') continue;
    if (path === root) return '.';
    if (path.startsWith(withSlash(root))) return path.slice(withSlash(root).length);
  }
  return path;
}

/**
 * Removes the thread's directory from free text such as a tool title, so lines stay short.
 * Only at the start of a path: a directory name that merely appears inside a longer path is left alone.
 */
export function shortenPaths(text: string, roots: string[]): string {
  let out = text;
  // Longest first, so a root nested in another is not cut short by the outer one.
  const ordered = [...new Set(roots)].filter((r) => r && r !== '/').sort((a, b) => b.length - a.length);
  for (const root of ordered) {
    const escaped = withSlash(root).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    out = out.replace(new RegExp(`(^|[\\s"'\`(=:,])${escaped}`, 'g'), '$1');
  }
  return out;
}

// The hub stores a message's pictures as <data dir>/attachments/<thread id>/<image id>.<ext>.
const ATTACHMENT_PATH = /(?:\/[^\s"'`]*)?\/attachments\/[0-9a-f-]{36}\/[0-9a-f-]{36}\.(?:png|jpg|gif|webp)/gi;

/** Replaces the paths of pictures the user sent with a name, where an agent mentions them (it opens them by path). */
export function nameAttachments(text: string): string {
  return text.replace(ATTACHMENT_PATH, t('view.sentImage'));
}

/** File names the hub can show as pictures. Whether a file really is one is for the daemon to say. */
export const IMAGE_FILE = /\.(?:png|jpe?g|gif|webp|svg)$/i;

/** File names the browser can play itself. Shown with a player rather than as binary. */
export const VIDEO_FILE = /\.(?:mp4|m4v|mov|webm)$/i;

export type LinkTarget =
  /** An address on the web: opens in a tab of its own, so the hub stays where it is. */
  | { kind: 'web'; url: string }
  /** A file on the agent's host. The browser cannot go there; `path` is what the daemon is asked for. */
  | { kind: 'file'; path: string }
  /** A place on this page, such as a footnote. */
  | { kind: 'anchor' }
  /** Nothing that should be followed. */
  | { kind: 'none' };

/**
 * Where a link in an answer leads. Agents link the files they wrote, as `file://` addresses or as
 * bare paths; followed like a web link, either would take the browser away from the thread, to a
 * page that is not there.
 */
export function linkTarget(href: string): LinkTarget {
  const raw = href.trim();
  if (!raw) return { kind: 'none' };
  if (raw.startsWith('#')) return { kind: 'anchor' };
  if (/^(?:https?:\/\/|mailto:|tel:)/i.test(raw)) return { kind: 'web', url: raw };
  const isFileUrl = /^file:\/\//i.test(raw);
  // Anything else with a scheme is not ours to follow. `a.ts:12` is a path with a line number, not a scheme.
  if (!isFileUrl && /^(?:[a-z][a-z0-9+.-]*:\/\/|(?:javascript|vbscript|data|blob):)/i.test(raw)) return { kind: 'none' };
  let path = isFileUrl ? raw.slice('file://'.length) : raw;
  try {
    // Markdown links arrive percent-encoded (spaces, Chinese file names).
    path = decodeURIComponent(path);
  } catch {
    // A stray "%" in a real file name: use it as written.
  }
  return path ? { kind: 'file', path } : { kind: 'none' };
}
