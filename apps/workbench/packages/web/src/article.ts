import type { TextFile } from './api';

/**
 * The writing mode's body, seen as an article rather than a file: `writing/` Markdown gets a
 * book-like preview and the plain-text page the person writes on. Everything here is about that
 * one view; the stages and drafts live in `writing.ts`.
 */

/** Is this Markdown file the writing mode's body: a `.md` anywhere under the thread's `writing/`? */
export function isArticleFile(file: TextFile): boolean {
  return file.text !== null && /\.md$/i.test(file.path) && file.path.startsWith(`${file.root}/writing/`);
}

/**
 * The body's word count, shown under the title. Chinese characters count one each; other scripts
 * count words, so a mixed text reads the way its writer would count it.
 */
const CJK = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/g;

export function countWords(text: string): number {
  const cjk = (text.match(CJK) ?? []).length;
  const words = (text.replace(CJK, ' ').match(/[A-Za-z0-9]+(?:['’][A-Za-z]+)?/g) ?? []).length;
  return cjk + words;
}

/* ------------------------------------------------------------------ */
/* The preview: markers written into the Markdown become colour, not punctuation. */

/** The pieces of a hast tree this walks. Kept local: no new dependency for two fields. */
interface HastNode {
  type: string;
  value?: string;
  tagName?: string;
  properties?: { className?: string[] } & Record<string, unknown>;
  children?: HastNode[];
}

/**
 * A rehype pass for the article preview: 【…】 turns into an orange chip without its brackets, and
 * a paragraph opened by 〔补〕 loses the mark and gains the green line of agent-added text.
 */
export function rehypeArticleMarks(): (tree: HastNode) => void {
  return (tree) => walk(tree);
}

function walk(node: HastNode): void {
  if (!node.children || node.tagName === 'pre' || node.tagName === 'code') return;
  const out: HastNode[] = [];
  for (const child of node.children) {
    if (child.type === 'text' && child.value?.includes('【')) out.push(...splitTodo(child.value));
    else {
      walk(child);
      out.push(child);
    }
  }
  node.children = out;
  if (node.type === 'element' && node.tagName === 'p') {
    const first = node.children[0];
    const value = first?.type === 'text' ? first.value : undefined;
    if (first && typeof value === 'string' && value.startsWith('〔补〕')) {
      first.value = value.replace(/^〔补〕\s?/, '');
      node.properties = { ...node.properties, className: [...(node.properties?.className ?? []), 'writing-added'] };
    }
  }
}

function splitTodo(value: string): HastNode[] {
  const out: HastNode[] = [];
  let last = 0;
  for (const found of value.matchAll(/【([^】]*)】/g)) {
    const at = found.index ?? 0;
    if (at > last) out.push({ type: 'text', value: value.slice(last, at) });
    out.push({ type: 'element', tagName: 'span', properties: { className: ['writing-todo'] }, children: [{ type: 'text', value: found[1] }] });
    last = at + found[0].length;
  }
  if (last < value.length) out.push({ type: 'text', value: value.slice(last) });
  return out;
}

/* ------------------------------------------------------------------ */
/* The page the person writes on: the Markdown's own marks, dimmed, under the typing. */

const escapeHtml = (text: string): string =>
  text.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] ?? c);
const faint = (text: string): string => `<span class="w-mk">${escapeHtml(text)}</span>`;

/**
 * The text as it is typed, with only its colours changed — never a size or a weight, so the layer
 * sits exactly behind the transparent textarea over it. Headings and fence lines dim entirely,
 * image and link syntax keeps its readable middle, and the writing mode's own marks (〔补〕, the
 * brackets of 【待补：…】) fade like the Markdown around them.
 */
export function highlightWritingSource(text: string): string {
  const lines: string[] = [];
  let fenced = false;
  for (const line of text.split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) {
      lines.push(faint(line));
      fenced = !fenced;
    } else if (fenced) lines.push(`<span class="w-code">${escapeHtml(line)}</span>`);
    else lines.push(inlineLine(line));
  }
  // A trailing newline makes no line of its own in a pre-wrap box; give the caret's empty one room.
  return lines.join('\n') + (text.endsWith('\n') || text === '' ? ' ' : '');
}

function inlineLine(line: string): string {
  const heading = line.match(/^(#{1,6})(\s+)(.*)$/);
  if (heading) return faint(`${heading[1]}${heading[2]}`) + inlineMarks(heading[3]);
  if (line.startsWith('〔补〕')) return faint('〔补〕') + inlineMarks(line.slice(3));
  return inlineMarks(line);
}

/** Image and link syntax, and the brackets around notes: dim the machinery, keep the words. */
const TOKEN = /!\[([^\]]*)\]\(([^)]*)\)|\[([^\]]*)\]\(([^)]*)\)|【|】/g;

function inlineMarks(text: string): string {
  let out = '';
  let last = 0;
  for (const found of text.matchAll(TOKEN)) {
    const at = found.index ?? 0;
    out += escapeHtml(text.slice(last, at));
    if (found[0] === '【' || found[0] === '】') out += faint(found[0]);
    else if (found[0].startsWith('!')) out += faint('![') + escapeHtml(found[1] ?? '') + faint(`](${found[2] ?? ''})`);
    else out += faint('[') + escapeHtml(found[3] ?? '') + faint(`](${found[4] ?? ''})`);
    last = at + found[0].length;
  }
  return out + escapeHtml(text.slice(last));
}
