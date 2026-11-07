/** One parser owns rendering, heading navigation and verbatim history excerpts. */
export type MarkdownBlock = {startLine: number; endLine: number} & (
  | {type: "code"; text: string}
  | {type: "table"; headers: string[]; rows: string[][]}
  | {type: "heading"; level: number; text: string}
  | {type: "list"; ordered: boolean; items: string[]}
  | {type: "paragraph"; lines: string[]}
);
type BlockContent = MarkdownBlock extends infer T ? T extends MarkdownBlock ? Omit<T, "startLine" | "endLine"> : never : never;

const UNORDERED = /^\s*[-*•]\s+(.*)$/;
const ORDERED = /^\s*\d{1,9}[.、)]\s+(.*)$/;

function tableCells(line: string) {
  return line.trim().replace(/^\|/, "").replace(/(?<!\\)\|$/, "").split(/(?<!\\)\|/).map(cell => cell.trim().replace(/\\\|/g, "|"));
}

export function parseMarkdownBlocks(text: string): MarkdownBlock[] {
  const lines = text.split("\n");
  const blocks: MarkdownBlock[] = [];
  let paragraph: string[] = [], paragraphStart = 0;
  const push = (block: BlockContent, startLine: number, endLine: number) => {
    blocks.push({...block, startLine, endLine});
  };
  const flushParagraph = () => {
    if (!paragraph.length) return;
    push({type: "paragraph", lines: paragraph}, paragraphStart, paragraphStart + paragraph.length);
    paragraph = [];
  };

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^\s{0,3}(`{3,}|~{3,})/u);
    if (fence) {
      flushParagraph();
      const start = i++, buffer: string[] = [];
      while (i < lines.length) {
        const close = lines[i].match(/^\s{0,3}(`+|~+)\s*$/u);
        if (close && close[1][0] === fence[1][0] && close[1].length >= fence[1].length) break;
        buffer.push(lines[i++]);
      }
      if (i < lines.length) i += 1;
      push({type: "code", text: buffer.join("\n")}, start, i);
      continue;
    }
    const heading = line.match(/^\s{0,3}(#{1,6})\s+(.*)$/u);
    if (heading) {
      flushParagraph();
      push({type: "heading", level: heading[1].length, text: heading[2].trim().replace(/\s+#+\s*$/u, "")}, i, i + 1);
      i += 1;
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length) {
      const headers = tableCells(line), separators = tableCells(lines[i + 1]);
      if (headers.length === separators.length && separators.every(cell => /^:?-{3,}:?$/.test(cell))) {
        flushParagraph();
        const start = i, rows: string[][] = [];
        i += 2;
        while (i < lines.length && lines[i].includes("|")) {
          const cells = tableCells(lines[i]);
          if (cells.length !== headers.length) break;
          rows.push(cells); i += 1;
        }
        push({type: "table", headers, rows}, start, i);
        continue;
      }
    }
    if (UNORDERED.test(line) || ORDERED.test(line)) {
      flushParagraph();
      const start = i, ordered = ORDERED.test(line), items: string[] = [];
      const pattern = ordered ? ORDERED : UNORDERED;
      while (i < lines.length) {
        const item = lines[i].match(pattern);
        if (!item) break;
        items.push(item[1]); i += 1;
      }
      push({type: "list", ordered, items}, start, i);
      continue;
    }
    if (line.trim() === "") {
      flushParagraph(); i += 1;
      continue;
    }
    if (!paragraph.length) paragraphStart = i;
    paragraph.push(line); i += 1;
  }
  flushParagraph();
  return blocks;
}

export type MarkdownHeading = {blockIndex: number; id: string; level: number; text: string};

/** Stable per-document anchors, including duplicate text and suffix collisions. */
export function markdownOutline(blocks: MarkdownBlock[], prefix: string, firstLevel = 2): MarkdownHeading[] {
  const headings = blocks.flatMap((block, blockIndex) => block.type === "heading" ? [{...block, blockIndex}] : []);
  const first = headings.reduce((level, heading) => Math.min(level, heading.level), 6);
  const used = new Set<string>();
  return headings.map(heading => {
    const slug = [...heading.text.normalize("NFKC").toLowerCase().replace(/[`*]/gu, "")
      .replace(/[^\p{L}\p{N}_-]+/gu, "-").replace(/^-+|-+$/gu, "")].slice(0, 80).join("") || "section";
    const base = `${prefix}-${slug}`;
    let id = base, occurrence = 1;
    while (used.has(id)) id = `${base}-${++occurrence}`;
    used.add(id);
    return {blockIndex: heading.blockIndex, id, text: heading.text,
      level: Math.min(6, Math.max(1, firstLevel + heading.level - first))};
  });
}

/** A prefix of complete original blocks; no clipped sentence, code, table or list. */
export function markdownExcerpt(text: string): string | null {
  if ([...text].length < 2400) return null;
  const blocks = parseMarkdownBlocks(text), lines = text.split("\n");
  let contentBlocks = 0;
  for (const [index, block] of blocks.entries()) {
    if (block.type === "heading") continue;
    contentBlocks += 1;
    const excerpt = lines.slice(0, block.endLine).join("\n");
    if (contentBlocks < 3 && [...excerpt].length < 1000) continue;
    return index < blocks.length - 1 ? excerpt : null;
  }
  return null;
}
