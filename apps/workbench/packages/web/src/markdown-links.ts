import type { Root, Content, PhrasingContent } from 'mdast';

/**
 * Punctuation that closes a URL in prose but never belongs to one: full-width marks (this
 * remark-gfm swallows whole Chinese sentences into literal autolinks) and the ASCII tail.
 */
// Full-width punctuation as escapes, so the source itself stays free of interface text.
const CJK_STOP = '\uff08\uff09\uff3b\uff3d\u3010\u3011\u300a\u300b\u3008\u3009\u300c\u300d\u300e\u300f'
  + '\uff1a\uff0c\u3002\uff1b\uff01\uff1f\u3001\u00b7\u2026\u2014\uff5e\u201c\u201d\u2018\u2019';
const TRAILING_JUNK = new Set([...'()[]{}<>,;:!?']);

type TextNode = Extract<PhrasingContent, { type: 'text' }>;

const asText = (node: PhrasingContent): TextNode | null => (node.type === 'text' ? (node as TextNode) : null);
const firstStop = (value: string) => {
  let at = value.length;
  for (const mark of CJK_STOP) {
    const i = value.indexOf(mark);
    if (i >= 0 && i < at) at = i;
  }
  return at;
};

/** The URL with the prose junk autolinking added cut off, plus the junk itself. */
function splitAddress(value: string): [string, string] {
  let kept = value;
  // Emphasis markers and full-width punctuation are never part of an address here.
  const star = kept.indexOf('*');
  if (star > 0) kept = kept.slice(0, star);
  const stop = firstStop(kept);
  if (stop > 0 && stop < kept.length) kept = kept.slice(0, stop);
  while (kept.length > 1 && TRAILING_JUNK.has(kept[kept.length - 1]!)) kept = kept.slice(0, -1);
  return [kept, value.slice(kept.length)];
}

/**
 * GFM's literal autolinks swallow what follows a URL: markdown emphasis markers, CJK
 * punctuation, even whole Chinese sentences end up percent-encoded inside the link,
 * leaving broken addresses and visible `**` markers in the text. This trims that junk
 * off and hands it back to the paragraph, so an emphasised address followed by CJK
 * punctuation renders as a bold link plus plain text.
 */
export function trimAutolinkJunk() {
  return (tree: Root, file: { toString(): string }) => {
    walk(tree, String(file));
  };
}

function walk(node: Content | Root, source: string) {
  const children = 'children' in node ? (node.children as Content[]) : undefined;
  if (!children) return;
  const next: Content[] = [];
  for (let i = 0; i < children.length; i++) {
    const child = children[i];
    walk(child, source);
    if (child.type === 'link' && child.children.length === 1) {
      const text = asText(child.children[0]);
      const start = child.position?.start.offset;
      const end = child.position?.end.offset;
      // Only bare GFM URLs may have swallowed prose; explicit links have their own boundaries.
      const raw = start !== undefined && end !== undefined ? source.slice(start, end) : '';
      if (text && /^(?:https?:\/\/|www\.)/i.test(raw) && raw === text.value) {
        const [kept, junk] = splitAddress(text.value);
        if (kept !== text.value) {
          child.url = /^www\./i.test(kept) ? `http://${kept}` : kept;
          text.value = kept;
          // `**address**（…）` lost its closing markers to the autolink: pair them up again
          // as real emphasis instead of leaving literal `**` in the text.
          const before = next.length ? asText(next[next.length - 1] as PhrasingContent) : null;
          const after = junk.startsWith('**') && before && before.value.endsWith('**');
          if (after) {
            before.value = before.value.slice(0, -2);
            next[next.length - 1] = before;
            next.push({ type: 'strong', children: [child] } as Content);
            next.push({ type: 'text', value: junk.slice(2) });
            continue;
          }
          next.push(child);
          if (junk) next.push({ type: 'text', value: junk });
          continue;
        }
      }
    }
    next.push(child);
  }
  (node as { children: Content[] }).children = next;
}

/**
 * What a message needs to know to turn its mentions of other threads into jump links: the threads
 * the person can navigate to, by id and by exact title. Titles repeat (the autotitle scratch
 * threads for one), so a title alone is never enough — the parenthesised id prefix must agree with
 * the same thread.
 */
export interface ThreadLinkIndex {
  titles: Map<string, { id: string; title: string }[]>;
  ids: Map<string, { id: string; title: string }>;
}

export function threadLinkIndex(threads: Array<{ id: string; title: string }>): ThreadLinkIndex {
  const index: ThreadLinkIndex = { titles: new Map(), ids: new Map() };
  for (const thread of threads) {
    if (!thread.id || !thread.title) continue;
    const known = { id: thread.id.toLowerCase(), title: thread.title };
    index.ids.set(known.id, known);
    const titled = index.titles.get(thread.title);
    if (titled) titled.push(known);
    else index.titles.set(thread.title, [known]);
  }
  return index;
}

/** A title in corner brackets with a short id in parentheses after it, as the assistant writes it. */
const TITLE_REF = /「([^」]{1,200}?)」[（(]([0-9a-fA-F]{6,36})[）)]/g;
/** A whole thread id in prose, as the daemon's own protocol messages quote them. */
const WHOLE_ID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g;

function threadLink(index: ThreadLinkIndex, id: string, label: string): PhrasingContent {
  const thread = index.ids.get(id);
  return { type: 'link', url: `thread://${id}`, title: thread?.title, children: [{ type: 'text', value: label }] };
}

/** Splits one text node into text and jump links for every thread mention it carries. */
function linkMentions(value: string, index: ThreadLinkIndex): PhrasingContent[] | null {
  const found: Array<{ at: number; end: number; node: PhrasingContent }> = [];
  for (const match of value.matchAll(TITLE_REF)) {
    // The title must belong to a thread whose id also starts with the quoted prefix; anything
    // else (a document, a PR) keeps its plain text.
    const ref = match[2]!.toLowerCase();
    const candidates = (index.titles.get(match[1]!) ?? []).filter(thread => thread.id.startsWith(ref));
    if (candidates.length === 1) found.push({ at: match.index!, end: match.index! + match[0].length, node: threadLink(index, candidates[0]!.id, match[0]) });
  }
  for (const match of value.matchAll(WHOLE_ID)) {
    const id = match[0].toLowerCase();
    if (index.ids.has(id)) found.push({ at: match.index!, end: match.index! + match[0].length, node: threadLink(index, id, match[0]) });
  }
  if (!found.length) return null;
  found.sort((a, b) => a.at - b.at);
  const out: PhrasingContent[] = [];
  let at = 0;
  for (const item of found) {
    if (item.at < at) continue; // inside an earlier match: that one stands
    if (item.at > at) out.push({ type: 'text', value: value.slice(at, item.at) });
    out.push(item.node);
    at = item.end;
  }
  if (at < value.length) out.push({ type: 'text', value: value.slice(at) });
  return out;
}

/**
 * Turns plain mentions of other threads into jump links: a corner-bracketed title with an id
 * prefix where both name the same thread, and a whole id the daemon is known to have. A link's
 * own label is left alone, so a web address that happens to contain an id is not rewired.
 */
export function threadMentions(index: ThreadLinkIndex) {
  return () => (tree: Root) => {
    descend(tree, index);
  };
}

function descend(node: Content | Root, index: ThreadLinkIndex) {
  const children = 'children' in node ? (node.children as Content[]) : undefined;
  if (!children) return;
  for (const child of children) {
    if (child.type === 'link') continue; // its label already goes somewhere
    descend(child, index);
  }
  const next: Content[] = [];
  let changed = false;
  for (const child of children) {
    if (child.type === 'text') {
      const replaced = linkMentions(child.value, index);
      if (replaced) { next.push(...(replaced as Content[])); changed = true; continue; }
    }
    next.push(child);
  }
  if (changed) (node as { children: Content[] }).children = next;
}
