/**
 * Where an answer's pictures go: right after the passage that names them, not all at the bottom.
 *
 * A remark plugin, given each picture's path and where in the text it is first named. It finds the
 * block holding that place (a paragraph, a heading, a table, a code block) and puts a strip of
 * pictures after it, inside the same list item or quote. A picture named nowhere such a block can
 * hold it goes to a strip at the very end. The strip is an element Markdown draws as a `div` with
 * the class `file-images` and the paths in `data-paths`; the component for `div` puts the pictures up.
 *
 * The words that named each picture then give way to it, as Markdown's own `![](…)` never shows
 * the address: a path written in the text or a link whose words are the path is taken out, and a
 * line left with nothing but separators goes with them — the picture is the line now. Paths inside
 * code blocks stay: there they are part of what was run, not a caption.
 */

interface Node {
  type: string;
  value?: string;
  url?: string;
  position?: { start: { offset?: number }; end: { offset?: number } };
  children?: Node[];
  data?: Record<string, unknown>;
}

export interface ImagePlace {
  path: string;
  at: number;
}

/** Blocks a picture can follow. Everything an answer says sits in one of these. */
const HOSTS = new Set(['paragraph', 'heading', 'table', 'code', 'html']);

export const IMAGE_STRIP_CLASS = 'file-images';

function strip(paths: string[]): Node {
  return { type: 'imageStrip', data: { hName: 'div', hProperties: { className: [IMAGE_STRIP_CLASS], dataPaths: JSON.stringify(paths) } } };
}

export function placeImages(places: ImagePlace[]) {
  return () => (tree: Node) => {
    if (!places.length) return;
    const left = new Set(places);
    const visit = (parent: Node) => {
      const children = parent.children ?? [];
      for (let i = 0; i < children.length; i++) {
        const node = children[i];
        if (!HOSTS.has(node.type)) {
          visit(node);
          continue;
        }
        const from = node.position?.start.offset;
        const to = node.position?.end.offset;
        if (from === undefined || to === undefined) continue;
        const here = places.filter((p) => left.has(p) && p.at >= from && p.at < to);
        if (!here.length) continue;
        for (const p of here) left.delete(p);
        children.splice(i + 1, 0, strip(here.map((p) => p.path)));
        i++;
      }
    };
    visit(tree);
    if (left.size) (tree.children ??= []).push(strip([...left].map((p) => p.path)));
    removeMentions(tree, places.map((p) => p.path));
  };
}

/** What a path may be made of, so a longer word around a hit is never mistaken for a mention. */
const PATH_CHAR = /[\p{L}\p{N}_@%+.~\-/]/u;
/** Separators a mention may lean on, on either side; a line left with nothing else says nothing. */
const LEAN_BEFORE = /[\s、，,;；（(]/;
const LEAN_AFTER = /[\s、，,;；）)]/;
const SEPARATORS_ONLY = /^[\s、，,;；·（）()]*$/;

/** The path as it may be written in the text: plain, or with the file scheme in front. */
const formsOf = (paths: string[]) => paths.flatMap((path) => [path, `file://${path}`]);

/** Every place a path stands alone in the value, cut out, with a separator it leaned on. */
function cutMentions(value: string, forms: string[]): string {
  let out = value;
  for (const form of forms) {
    let at = out.indexOf(form);
    while (at >= 0) {
      const end = at + form.length;
      const before = at > 0 ? out[at - 1]! : '';
      const after = end < out.length ? out[end]! : '';
      // A '.' or ':' may sit right on the end of the path and still bound it, as the finder itself
      // allows: a sentence's stop, or "name:" — but only when nothing word-like follows it.
      const afterBounds = !after || !PATH_CHAR.test(after)
        || ((after === '.' || after === ':') && (end + 1 >= out.length || /\s/.test(out[end + 1]!)));
      if ((before && PATH_CHAR.test(before)) || !afterBounds) {
        at = out.indexOf(form, at + 1);
        continue;
      }
      let from = at;
      let to = end;
      const tail = to < out.length ? out[to]! : '';
      // One separator goes with the cut, never one on each side: the words around it must not glue.
      if (LEAN_AFTER.test(tail)) to++; // the punctuation it leaned on
      else if ((tail === '.' || tail === '。') && (to + 1 >= out.length || /\s/.test(out[to + 1]!))) to++; // its sentence's stop
      else if (from > 0 && LEAN_BEFORE.test(out[from - 1]!)) from--; // the space it was given
      out = out.slice(0, from) + out.slice(to);
      at = out.indexOf(form); // the text shifted: look from the start again
    }
  }
  return out;
}

/** The words a link shows: the text and code inside it, joined. */
function shownBy(node: Node): string {
  return (node.children ?? [])
    .map((child) => (child.type === 'text' || child.type === 'inlineCode' ? child.value ?? '' : child.children ? shownBy(child) : ''))
    .join('');
}

const isStrip = (node: Node) => node.type === 'imageStrip';
const saysNothing = (node: Node) => node.type === 'break' || ((node.type === 'text' || node.type === 'inlineCode') && SEPARATORS_ONLY.test(node.value ?? ''));

/**
 * Takes the words that named each placed picture out of the tree, in every block, and what a
 * removal emptied with them. A link whose words are the path goes whole; one with a label of its
 * own stays — the label is prose, and still opens the picture. A block that now shows nothing but
 * separators is dropped, and a list item or quote left holding only that; a block holding a strip
 * always stays, the strip being what the emptied line said.
 */
function removeMentions(tree: Node, paths: string[]): void {
  const forms = formsOf(paths);
  const prune = (node: Node): boolean => {
    const children = node.children;
    if (!children) {
      if (node.type === 'text' || node.type === 'inlineCode') {
        node.value = cutMentions(node.value ?? '', forms);
        return node.value === '';
      }
      return false; // an image, a break, inline html: not ours to cut, and never "nothing"
    }
    const kept: Node[] = [];
    for (const child of children) {
      if (isStrip(child)) {
        kept.push(child);
        continue;
      }
      if (child.type === 'link') {
        const url = String(child.url ?? '').replace(/^file:\/\//, '');
        if (forms.includes(url) && SEPARATORS_ONLY.test(cutMentions(shownBy(child), forms))) continue;
        kept.push(child); // a label of its own: prose, and a way to open the picture
        continue;
      }
      if (prune(child)) continue;
      kept.push(child);
    }
    node.children = kept;
    if (kept.some(isStrip)) return false;
    return kept.length === 0 || kept.every(saysNothing);
  };
  prune(tree);
}

/** The paths a strip carries, read back from the element Markdown drew. */
export function stripPaths(props: { className?: string; 'data-paths'?: unknown }): string[] | null {
  if (!props.className?.split(' ').includes(IMAGE_STRIP_CLASS) || typeof props['data-paths'] !== 'string') return null;
  try {
    const paths: unknown = JSON.parse(props['data-paths']);
    return Array.isArray(paths) && paths.every((p) => typeof p === 'string') ? paths : null;
  } catch {
    return null;
  }
}
