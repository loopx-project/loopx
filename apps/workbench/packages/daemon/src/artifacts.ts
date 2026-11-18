import { realpathSync, statSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { isAbsolute, join, normalize, relative, resolve, sep } from 'node:path';

/** Exact local file references in an answer, never a substring of a longer path or URL. */
export function referencesFile(text: string, path: string): boolean {
  const matches = (raw: string, encoded = false) => {
    if (raw.length > 4096) return false;
    const fileUrl = /^file:\/\//i.test(raw);
    let value = fileUrl ? raw.slice(7) : raw;
    if (encoded || fileUrl) {
      try { value = decodeURIComponent(value); } catch { /* A literal percent sign. */ }
    }
    value = value.replace(/(?::\d+(?::\d+)?|#L\d+(?:-L?\d+)?)$/, '');
    if (!fileUrl && value.startsWith('~/')) value = resolve(homedir(), value.slice(2));
    return isAbsolute(value) && !value.startsWith('//') && normalize(value) === path;
  };
  // Markdown destinations may contain spaces in angle brackets, or balanced parentheses.
  for (const m of text.matchAll(/\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|((?:\\.|[^\\\s()]|\([^()\s]*\))+))(?:\s+"[^"\n]*")?\s*\)/g)) {
    if (matches((m[1] ?? m[2]).replace(/\\([\\()[\]<> ])/g, '$1'), true)) return true;
  }
  for (const m of text.matchAll(/([`'"])((?:(?:file:\/\/)?\/|~\/)[^\r\n]*?)\1|<((?:file:\/\/)?\/[^>\r\n]+)>/gi)) {
    if (matches(m[2] ?? m[3])) return true;
  }
  for (const m of text.matchAll(/(?<=^|[\s`'"(（：，。])((?:(?:file:\/\/)?\/|~\/)[^\s`'"<>()[\]，。；！？]+)/gi)) {
    if (matches(m[1].replace(/[.,;!?：]+$/, ''))) return true;
  }
  return false;
}

/**
 * A single ordinary temporary file. Resolve the system temp root, but reject symlinks beneath
 * it: a referenced path must not become a route into another directory or another file.
 * This grants neither a directory listing nor access to neighbouring files.
 */
export function temporaryFile(path: string): string | null {
  if (!isAbsolute(path) || path.startsWith('//')) return null;
  const candidate = normalize(path);
  for (const root of new Set([tmpdir(), '/tmp', '/var/tmp'])) {
    try {
      const base = realpathSync(root);
      if (base === sep) continue;
      for (const spelling of new Set([normalize(root), base])) {
        const rel = relative(spelling, candidate);
        if (!rel || rel === '..' || rel.startsWith(`..${sep}`) || isAbsolute(rel)) continue;
        const real = realpathSync(candidate);
        if (real !== join(base, rel) || !statSync(real).isFile()) continue;
        return real;
      }
    } catch {
      // Missing, inaccessible, or removed while resolving it.
    }
  }
  return null;
}

function underTemporaryRoot(candidate: string): boolean {
  for (const root of new Set([tmpdir(), '/tmp', '/var/tmp'])) {
    let base: string;
    try {
      base = realpathSync(root);
    } catch {
      continue;
    }
    if (base === sep) continue;
    for (const spelling of new Set([normalize(root), base])) {
      const rel = relative(spelling, candidate);
      if (rel && rel !== '..' && !rel.startsWith(`..${sep}`) && !isAbsolute(rel)) return true;
    }
  }
  return false;
}

/**
 * The regular file an answer's absolute path names, wherever it is. Symlinks are followed (a home
 * directory is often one), except beneath a shared temp root, where another user could plant a
 * link to a private file: there the stricter rule of temporaryFile applies.
 */
export function referencedFile(path: string): string | null {
  if (!isAbsolute(path) || path.startsWith('//')) return null;
  const candidate = normalize(path);
  if (underTemporaryRoot(candidate)) return temporaryFile(candidate);
  try {
    const real = realpathSync(candidate);
    return statSync(real).isFile() ? real : null;
  } catch {
    return null;
  }
}
