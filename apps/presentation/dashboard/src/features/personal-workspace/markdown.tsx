import { Fragment, type ReactNode } from "react";

/**
 * Minimal, safe Markdown renderer for visible Agent prose.
 * Builds React nodes directly (no dangerouslySetInnerHTML), so raw HTML in
 * model output renders as inert text. Supports the subset LoopX Agents
 * actually emit in chat: fenced code, inline code, bold, links, headings,
 * and ordered/unordered lists.
 */

const INLINE_PATTERN = /(`[^`\n]+`)|(\*\*[^*\n]+(\*[^*\n]*)?\*\*)|(\[[^\]\n]{1,120}\]\(https?:\/\/[^)\s]+\))/g;

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  let index = 0;
  for (const match of text.matchAll(INLINE_PATTERN)) {
    const at = match.index ?? 0;
    if (at > last) nodes.push(text.slice(last, at));
    const token = match[0];
    const key = `${keyPrefix}-i${index++}`;
    if (token.startsWith("`")) {
      nodes.push(<code className="personal-md-code" key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("**")) {
      nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else {
      const close = token.indexOf("](");
      const label = token.slice(1, close);
      const href = token.slice(close + 2, -1);
      nodes.push(<a className="personal-md-link" href={href} key={key} rel="noreferrer" target="_blank">{label}</a>);
    }
    last = at + token.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

type Block =
  | { type: "code"; text: string }
  | { type: "table"; headers: string[]; rows: string[][] }
  | { type: "heading"; level: number; text: string }
  | { type: "list"; ordered: boolean; items: string[] }
  | { type: "paragraph"; lines: string[] };

const UNORDERED = /^\s*[-*•]\s+(.*)$/;
const ORDERED = /^\s*\d{1,2}[.、)]\s+(.*)$/;

function tableCells(line: string) {
  return line.trim().replace(/^\|/, "").replace(/(?<!\\)\|$/, "").split(/(?<!\\)\|/).map(cell => cell.trim().replace(/\\\|/g, "|"));
}

function parseBlocks(text: string, report: boolean): Block[] {
  const lines = text.split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  const flushParagraph = () => {
    if (paragraph.length > 0) {
      blocks.push({ type: "paragraph", lines: paragraph });
      paragraph = [];
    }
  };

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      flushParagraph();
      const buffer: string[] = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        buffer.push(lines[i]);
        i += 1;
      }
      i += 1; // closing fence, or end of text
      blocks.push({ type: "code", text: buffer.join("\n") });
      continue;
    }
    const heading = line.match(/^\s{0,3}#{1,4}\s+(.*)$/);
    if (heading) {
      flushParagraph();
      const marks = line.trimStart().match(/^#+/)?.[0].length ?? 1;
      blocks.push({ type: "heading", level: marks, text: heading[1].trim() });
      i += 1;
      continue;
    }
    // Report tables are opt-in; ordinary chat keeps its existing rendering.
    if (report && line.includes("|") && i + 1 < lines.length) {
      const headers = tableCells(line), separators = tableCells(lines[i + 1]);
      if (headers.length === separators.length && separators.every(cell => /^:?-{3,}:?$/.test(cell))) {
        flushParagraph();
        const rows: string[][] = [];
        i += 2;
        while (i < lines.length && lines[i].includes("|")) {
          const cells = tableCells(lines[i]);
          if (cells.length !== headers.length) break;
          rows.push(cells); i++;
        }
        blocks.push({type: "table", headers, rows});
        continue;
      }
    }
    if (UNORDERED.test(line) || ORDERED.test(line)) {
      flushParagraph();
      const ordered = ORDERED.test(line);
      const pattern = ordered ? ORDERED : UNORDERED;
      const items: string[] = [];
      while (i < lines.length) {
        const item = lines[i].match(pattern);
        if (!item) break;
        items.push(item[1]);
        i += 1;
      }
      blocks.push({ type: "list", ordered, items });
      continue;
    }
    if (line.trim() === "") {
      flushParagraph();
      i += 1;
      continue;
    }
    paragraph.push(line);
    i += 1;
  }
  flushParagraph();
  return blocks;
}

export function MarkdownText({ text, report = false }: { text: string; report?: boolean }) {
  return (
    <div className="personal-md">
      {parseBlocks(text, report).map((block, index) => {
        const key = `b${index}`;
        if (block.type === "code") {
          return <pre className="personal-md-pre" key={key}><code>{block.text}</code></pre>;
        }
        if (block.type === "heading") {
          return <p className={`personal-md-heading is-h${block.level}`} key={key}>{renderInline(block.text, key)}</p>;
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
