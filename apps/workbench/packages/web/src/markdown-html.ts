import type { Content, Root } from 'mdast';
import type { Options } from 'react-markdown';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import { IMAGE_STRIP_CLASS } from './image-places';

type RehypePlugins = NonNullable<Options['rehypePlugins']>;
type Schema = NonNullable<Parameters<typeof rehypeSanitize>[0]>;

/**
 * GitHub-style HTML allowlist for Markdown output: renders safe inline/block HTML such as `<br>`,
 * `<details>/<summary>`, `<kbd>`, `<sub>/<sup>`, `<mark>`, and `<u>`, while stripping scripts,
 * event handlers, iframes, and unsafe URL schemes.
 *
 * Extends the default GFM schema only where this app already has its own trusted conventions:
 * - `file:` and `thread:` link/image protocols handled by `MarkdownLink` and `MarkdownImage`;
 * - `<div class="file-images" data-paths="...">` strips injected by `placeImages`.
 */
export const MARKDOWN_HTML_SCHEMA: Schema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), 'mark', 'u'],
  attributes: {
    ...defaultSchema.attributes,
    div: [...(defaultSchema.attributes?.div ?? []), ['className', IMAGE_STRIP_CLASS], 'dataPaths'],
  },
  protocols: {
    ...defaultSchema.protocols,
    href: [...(defaultSchema.protocols?.href ?? []), 'file', 'thread'],
    src: [...(defaultSchema.protocols?.src ?? []), 'file'],
  },
};

/**
 * Standard HTML tag names (both allowed by GFM and disallowed ones that `rehype-sanitize` should
 * strip rather than echo verbatim). Any lone `<placeholder>` whose tag name is not in this set
 * (e.g. `<user_id>`, `Promise<T>`, `<api-key>`) is treated as literal text so raw HTML parsing
 * never swallows prose placeholders.
 */
const KNOWN_HTML_TAGS = new Set<string>([
  ...(MARKDOWN_HTML_SCHEMA.tagNames ?? []),
  'abbr', 'address', 'applet', 'area', 'article', 'aside', 'audio', 'base', 'bdi', 'bdo',
  'big', 'blink', 'body', 'button', 'canvas', 'caption', 'center', 'cite', 'col', 'colgroup',
  'data', 'datalist', 'dfn', 'dialog', 'embed', 'fieldset', 'figcaption', 'figure', 'font',
  'footer', 'form', 'frame', 'frameset', 'head', 'header', 'hgroup', 'html', 'iframe', 'keygen',
  'label', 'legend', 'link', 'main', 'map', 'marquee', 'math', 'menu', 'meta', 'meter', 'nav',
  'noframes', 'noscript', 'object', 'optgroup', 'option', 'output', 'param', 'portal',
  'progress', 'script', 'select', 'slot', 'small', 'style', 'svg', 'template', 'textarea',
  'time', 'title', 'track', 'video', 'wbr', 'xmp',
]);

/** Matches a single standalone opening, closing, or self-closing tag with no nested markup. */
const LONE_TAG = /^<\/?\s*([a-zA-Z][a-zA-Z0-9_-]*)(?:\s[^<>]*)?\/?>$/;

/** Block containers where a standalone `html` node must become a paragraph, not bare root text. */
const BLOCK_PARENTS = new Set(['root', 'blockquote', 'listItem', 'footnoteDefinition']);

/**
 * Remark plugin that turns unrecognised `<placeholder>` tags into plain text before `rehype-raw`
 * parses raw HTML. Standard HTML tags stay `html` nodes so `rehype-raw` + `rehype-sanitize` render
 * allowlisted GFM markup and strip dangerous tags like `<script>` or `<iframe>`.
 */
export function preserveNonHtmlTags() {
  return (tree: Root) => {
    walk(tree);
  };
}

function walk(node: Content | Root) {
  const children = 'children' in node ? (node.children as Content[]) : undefined;
  if (!children) return;
  const blockParent = BLOCK_PARENTS.has(node.type);
  for (let i = 0; i < children.length; i++) {
    const child = children[i]!;
    if (child.type === 'html') {
      const trimmed = child.value.trim();
      const match = LONE_TAG.exec(trimmed);
      if (match && !KNOWN_HTML_TAGS.has(match[1]!.toLowerCase())) {
        const textNode: Content = { type: 'text', value: child.value };
        children[i] = blockParent ? ({ type: 'paragraph', children: [textNode] } as Content) : textNode;
        continue;
      }
    }
    walk(child);
  }
}

/** Shared rehype pipeline: parse inline/block HTML, then sanitize with the GFM allowlist. */
export const MARKDOWN_REHYPE_PLUGINS: RehypePlugins = [
  rehypeRaw,
  [rehypeSanitize, MARKDOWN_HTML_SCHEMA],
];
