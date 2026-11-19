/**
 * The pure parts of the file browser: what a link to a file points at, where a file sits under its
 * thread's folder, which language it is written in, and how highlighted code splits into lines.
 */

/** A file and, when the link names one, the line to show. */
export interface FileRef {
  path: string;
  line?: number;
}

/**
 * Agents point at a place in a file as `a.ts:12`, `a.ts:12:5`, `a.ts#L12` or `a.ts#L12-L20`. The
 * line is kept and the rest of the suffix dropped. A name that only ends in digits (`v2`) is a name.
 */
export function fileRef(raw: string): FileRef {
  const m = /^(.*?)(?::(\d+)(?::\d+)?|#L(\d+)(?:-L?\d+)?)$/.exec(raw);
  if (!m || !m[1]) return { path: raw };
  const line = Number(m[2] ?? m[3]);
  return line > 0 ? { path: m[1], line } : { path: m[1] };
}

/** A step in the path from the thread's folder down to a file or folder. */
export interface Crumb {
  name: string;
  path: string;
}

/** From `root` down to `path`, one step per folder; `root` itself first. A path outside `root` is just itself. */
export function crumbsOf(root: string, path: string): Crumb[] {
  const base = root.replace(/\/+$/, '') || '/';
  const first = { name: base.split('/').filter(Boolean).pop() ?? '/', path: base };
  if (path === base) return [first];
  const prefix = base === '/' ? '/' : `${base}/`;
  if (!path.startsWith(prefix)) return [{ name: path.split('/').filter(Boolean).pop() ?? path, path }];
  const crumbs = [first];
  let at = base;
  for (const part of path.slice(prefix.length).split('/').filter(Boolean)) {
    at = at === '/' ? `/${part}` : `${at}/${part}`;
    crumbs.push({ name: part, path: at });
  }
  return crumbs;
}

/** The folder a file is in. */
export const parentDir = (path: string) => path.replace(/\/[^/]+\/?$/, '') || '/';

/** Every folder from `root` down to the one `path` sits in, for opening the tree on a file. */
export function foldersTo(root: string, path: string): string[] {
  return crumbsOf(root, path)
    .slice(0, -1)
    .map((c) => c.path);
}

/** A link inside a Markdown file, made absolute against the folder the file is in. Null for one that is not to a file. */
export function resolveRelative(dir: string, href: string): string | null {
  if (!href || /^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith('#')) return null;
  const clean = href.replace(/[?#].*$/, '');
  if (!clean) return null;
  const parts = (clean.startsWith('/') ? clean : `${dir}/${clean}`).split('/');
  const out: string[] = [];
  for (const part of parts) {
    if (!part || part === '.') continue;
    if (part === '..') out.pop();
    else out.push(part);
  }
  return `/${out.join('/')}`;
}

/** File sizes as people read them. */
export function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

const BY_EXTENSION: Record<string, string> = {
  ts: 'typescript',
  tsx: 'typescript',
  mts: 'typescript',
  cts: 'typescript',
  js: 'javascript',
  jsx: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  json: 'json',
  jsonl: 'json',
  py: 'python',
  go: 'go',
  rs: 'rust',
  java: 'java',
  kt: 'kotlin',
  kts: 'kotlin',
  swift: 'swift',
  c: 'c',
  h: 'c',
  cc: 'cpp',
  cpp: 'cpp',
  hpp: 'cpp',
  cs: 'csharp',
  rb: 'ruby',
  php: 'php',
  sh: 'bash',
  bash: 'bash',
  zsh: 'bash',
  css: 'css',
  scss: 'scss',
  html: 'xml',
  xml: 'xml',
  svg: 'xml',
  vue: 'xml',
  md: 'markdown',
  markdown: 'markdown',
  yaml: 'yaml',
  yml: 'yaml',
  toml: 'ini',
  ini: 'ini',
  sql: 'sql',
  diff: 'diff',
  patch: 'diff',
  lua: 'lua',
  proto: 'protobuf',
  thrift: 'thrift',
};

const BY_NAME: Record<string, string> = {
  dockerfile: 'dockerfile',
  makefile: 'makefile',
  'cmakelists.txt': 'cmake',
  '.bashrc': 'bash',
  '.zshrc': 'bash',
  '.gitignore': 'bash',
  '.env': 'bash',
};

/** The highlighting language for a file, by its name. Null: shown as plain text. */
export function languageOf(path: string): string | null {
  const name = path.slice(path.lastIndexOf('/') + 1).toLowerCase();
  if (BY_NAME[name]) return BY_NAME[name];
  if (name.startsWith('dockerfile')) return 'dockerfile';
  const dot = name.lastIndexOf('.');
  return dot > 0 ? (BY_EXTENSION[name.slice(dot + 1)] ?? null) : null;
}

/**
 * Highlighted HTML cut into lines, each closing the spans still open at its end and opening them
 * again at the start of the next, so every line can be drawn on its own row next to its number.
 * The HTML is the highlighter's: `<span class="…">`, `</span>` and escaped text, nothing else.
 */
export function splitHighlighted(html: string): string[] {
  const lines: string[] = [];
  const open: string[] = [];
  let current = '';
  const tag = /<span[^>]*>|<\/span>|\n/g;
  let last = 0;
  for (let m = tag.exec(html); m; m = tag.exec(html)) {
    current += html.slice(last, m.index);
    last = tag.lastIndex;
    if (m[0] === '\n') {
      lines.push(current + '</span>'.repeat(open.length));
      current = open.join('');
    } else if (m[0] === '</span>') {
      open.pop();
      current += m[0];
    } else {
      open.push(m[0]);
      current += m[0];
    }
  }
  lines.push(current + html.slice(last));
  return lines;
}

/** Plain text as HTML that shows the same characters. */
export const escapeHtml = (text: string) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
