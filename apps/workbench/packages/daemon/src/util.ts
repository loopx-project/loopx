import { accessSync, constants, existsSync } from 'node:fs';
import { delimiter, isAbsolute, join } from 'node:path';

/** True if argv[0] resolves to an executable: an absolute/relative path that exists, or a name on PATH. */
export function commandExists(bin: string): boolean {
  if (!bin) return false;
  if (bin.includes('/') || isAbsolute(bin)) {
    try {
      accessSync(bin, constants.X_OK);
      return true;
    } catch {
      return false;
    }
  }
  const dirs = (process.env.PATH ?? '').split(delimiter).filter(Boolean);
  return dirs.some((dir) => existsSync(join(dir, bin)));
}

/** Concatenates the text parts of an agent content payload. Unknown shapes yield ''. */
export function textOf(content: unknown): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) return content.map(textOf).join('');
  if (content && typeof content === 'object') {
    const c = content as { type?: string; text?: unknown; content?: unknown };
    if (typeof c.text === 'string') return c.text;
    if (c.content !== undefined) return textOf(c.content);
  }
  return '';
}

/**
 * What a message asks, for a title or a preview line: without the passages the page put in front
 * of it, numbered notes ("[注释 1]", quote lines, "评论：…") or a plain quote. A message that is
 * only notes is summed up by its first comment, else by the passage itself.
 */
export function askedText(text: string): string {
  text = text.replace(/(?:^|\n\n)<calli-references>\n[^\n]*\n<\/calli-references>(?=\n\n|$)/g, '');
  const lines = text.split('\n');
  const quoted: string[] = [];
  const comments: string[] = [];
  let inNote = false;
  let i = 0;
  for (; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim()) continue;
    if (/^\[注释 \d+\]$/.test(line.trim())) inNote = true;
    else if (line.startsWith('>')) quoted.push(line.replace(/^>\s?/, '').trim());
    else if (inNote && line.startsWith('评论：')) comments.push(line.slice('评论：'.length).trim());
    // A note on a file names it on its own line; the passage and comment are what was asked.
    else if (inNote && line.startsWith('文件：')) continue;
    else break;
  }
  const body = lines.slice(i).join('\n').trim();
  return body || comments.find(Boolean) || quoted.filter(Boolean).join(' ') || text.trim();
}
