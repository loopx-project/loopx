import { Fragment, useId, useMemo, type ReactNode } from "react";
import { LinkifyIt } from "linkify-it";
import {markdownOutline, parseMarkdownBlocks} from "./markdown-blocks";
import "./markdown.css";

/**
 * Minimal, safe Markdown renderer for visible Agent prose.
 * Builds React nodes directly (no dangerouslySetInnerHTML), so raw HTML in
 * model output renders as inert text. Supports the subset LoopX Agents
 * actually emit in chat and reports: fenced code, inline code, bold, web links,
 * headings, ordered/unordered lists and tables.
 */

const INLINE_PATTERN = /(`[^`\n]+`)|(\*\*[^*\n]+(\*[^*\n]*)?\*\*)/g;
const webLinks = new LinkifyIt({
  fuzzyLink: false, fuzzyEmail: false, urlAuth: true,
}).add("ftp:", null).add("mailto:", null).add("//", null);

/**
 * linkify-it bounds how far it scans userinfo, so an automatic match can
 * stop before the `@` that still belongs to the same address. The matched
 * prefix then has the userinfo as its host, and linking it would silently open
 * a different site than the text the reader sees. Only a match that ends the
 * address it started is turned into a link; an incomplete one stays inert text.
 */
function completeWebMatch(part: string, match: { lastIndex: number }): boolean {
  return !part.slice(match.lastIndex).startsWith("@");
}

function renderPlainText(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  // Unescaped CJK punctuation separates prose from bare URLs. Named Markdown
  // destinations bypass this split, retaining literal punctuation in the URL.
  for (const [partIndex, part] of text.split(/([。，、；：！？])/u).entries()) {
    let last = 0;
    for (const match of webLinks.match(part) ?? []) {
      if (match.index > last) nodes.push(part.slice(last, match.index));
      nodes.push(completeWebMatch(part, match)
        ? <a className="personal-md-link" href={match.url}
          key={`${keyPrefix}-p${partIndex}-${match.index}`} rel="noreferrer" target="_blank">{match.text}</a>
        : match.text);
      last = match.lastIndex;
    }
    if (last < part.length) nodes.push(part.slice(last));
  }
  return nodes;
}

function nextMarkdownLink(text: string, from: number): { start: number; end: number } | undefined {
  let searchFrom = from;
  while (searchFrom < text.length) {
    const start = text.indexOf("[", searchFrom);
    if (start < 0) return undefined;
    const labelEnd = text.indexOf("](", start + 1);
    if (labelEnd <= start + 1 || labelEnd - start > 121 || /[\n\r]/u.test(text.slice(start + 1, labelEnd))) {
      searchFrom = start + 1;
      continue;
    }
    const urlStart = labelEnd + 2;
    if (!/^https?:\/\//iu.test(text.slice(urlStart))) {
      searchFrom = start + 1;
      continue;
    }

    let depth = 0;
    let end = urlStart;
    for (; end < text.length; end += 1) {
      const char = text[end];
      if (!char || /\s/u.test(char)) break;
      if (char === "(") depth += 1;
      else if (char === ")") {
        if (depth === 0) break;
        depth -= 1;
      }
    }
    if (end > urlStart && end < text.length && text[end] === ")" && depth === 0) {
      return { start, end: end + 1 };
    }
    searchFrom = start + 1;
  }
  return undefined;
}

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  let index = 0;
  while (last < text.length) {
    INLINE_PATTERN.lastIndex = last;
    const match = INLINE_PATTERN.exec(text);
    const link = nextMarkdownLink(text, last);
    if (!match && !link) break;
    if (link && (!match || link.start < match.index)) {
      if (link.start > last) nodes.push(...renderPlainText(text.slice(last, link.start), `${keyPrefix}-t${index}`));
      const token = text.slice(link.start, link.end);
      const close = token.indexOf("](");
      const label = token.slice(1, close);
      const href = token.slice(close + 2, -1);
      nodes.push(<a className="personal-md-link" href={href} key={`${keyPrefix}-i${index++}`} rel="noreferrer" target="_blank">{label}</a>);
      last = link.end;
      continue;
    }
    if (!match) break;
    const at = match.index;
    if (at > last) nodes.push(...renderPlainText(text.slice(last, at), `${keyPrefix}-t${index}`));
    const token = match[0];
    const key = `${keyPrefix}-i${index++}`;
    if (token.startsWith("`")) {
      nodes.push(<code className="personal-md-code" key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("**")) {
      nodes.push(<strong key={key}>{renderInline(token.slice(2, -2), key)}</strong>);
    }
    last = at + token.length;
  }
  if (last < text.length) nodes.push(...renderPlainText(text.slice(last), `${keyPrefix}-t${index}`));
  return nodes;
}

export function MarkdownText({text, headingPrefix, headingLevel = 2}: {
  text: string;
  headingPrefix?: string;
  headingLevel?: 1 | 2 | 3 | 4 | 5 | 6;
}) {
  const instanceId = useId();
  const blocks = useMemo(() => parseMarkdownBlocks(text), [text]);
  const headings = markdownOutline(blocks, headingPrefix ?? `md-${instanceId}`, headingLevel);
  const headingsByBlock = new Map(headings.map(heading => [heading.blockIndex, heading]));
  return (
    <div className="personal-md">
      {blocks.map((block, index) => {
        const key = `b${index}`;
        if (block.type === "code") {
          return <pre className="personal-md-pre" tabIndex={0} key={key}><code>{block.text}</code></pre>;
        }
        if (block.type === "heading") {
          const heading = headingsByBlock.get(index)!;
          const Heading = `h${heading.level}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
          return <Heading id={heading.id} tabIndex={-1} className={`personal-md-heading is-h${block.level}`} key={key}>{renderInline(block.text, key)}</Heading>;
        }
        if (block.type === "table") {
          return <div className="personal-md-table-scroll" tabIndex={0} key={key}><table>
            <thead><tr>{block.headers.map((cell, n) => <th scope="col" key={n}>{renderInline(cell, `${key}-h${n}`)}</th>)}</tr></thead>
            <tbody>{block.rows.map((row, n) => <tr key={n}>{row.map((cell, c) => <td key={c}>{renderInline(cell, `${key}-${n}-${c}`)}</td>)}</tr>)}</tbody>
          </table></div>;
        }
        if (block.type === "list") {
          const items = block.items.map((item, itemIndex) => <li key={`${key}-${itemIndex}`}>{renderInline(item, `${key}-${itemIndex}`)}</li>);
          return block.ordered
            ? <ol className="personal-md-list" key={key}>{items}</ol>
            : <ul className="personal-md-list" key={key}>{items}</ul>;
        }
        return (
          <p key={key}>
            {block.lines.map((line, lineIndex) => (
              <Fragment key={`${key}-${lineIndex}`}>
                {lineIndex > 0 ? <br /> : null}
                {renderInline(line, `${key}-${lineIndex}`)}
              </Fragment>
            ))}
          </p>
        );
      })}
    </div>
  );
}
