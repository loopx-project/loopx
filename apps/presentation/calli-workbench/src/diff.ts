/**
 * Line diff for showing file changes. Pure, so it can be tested directly.
 * Agents report a change as the old and new text of the edited region, not as a patch.
 */

export interface DiffLine {
  type: 'ctx' | 'add' | 'del';
  text: string;
}

export interface DiffStats {
  added: number;
  removed: number;
}

/** Above this many table cells the LCS is skipped and the region is shown as removed-then-added. */
const MAX_CELLS = 1_000_000;

function splitLines(text: string): string[] {
  if (text === '') return [];
  const lines = text.split('\n');
  // A trailing newline ends the last line; it is not an extra empty line.
  if (lines[lines.length - 1] === '') lines.pop();
  return lines;
}

/** Lines of the change, in order. `oldText` null means the file is new. */
export function diffLines(oldText: string | null, newText: string): DiffLine[] {
  const a = splitLines(oldText ?? '');
  const b = splitLines(newText);

  // Unchanged lines at both ends are the common case and need no table.
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }

  const out: DiffLine[] = [];
  for (let i = 0; i < start; i++) out.push({ type: 'ctx', text: a[i] });
  out.push(...diffMiddle(a.slice(start, endA), b.slice(start, endB)));
  for (let i = endA; i < a.length; i++) out.push({ type: 'ctx', text: a[i] });
  return out;
}

function diffMiddle(a: string[], b: string[]): DiffLine[] {
  const del = (text: string): DiffLine => ({ type: 'del', text });
  const add = (text: string): DiffLine => ({ type: 'add', text });
  if (a.length === 0) return b.map(add);
  if (b.length === 0) return a.map(del);
  if (a.length * b.length > MAX_CELLS) return [...a.map(del), ...b.map(add)];

  // Longest common subsequence, filled from the end so the walk below runs forwards.
  const n = a.length;
  const m = b.length;
  const width = m + 1;
  const table = new Uint32Array((n + 1) * width);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      table[i * width + j] =
        a[i] === b[j] ? table[(i + 1) * width + j + 1] + 1 : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
    }
  }

  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ type: 'ctx', text: a[i] });
      i++;
      j++;
    } else if (table[(i + 1) * width + j] >= table[i * width + j + 1]) {
      out.push(del(a[i++]));
    } else {
      out.push(add(b[j++]));
    }
  }
  while (i < n) out.push(del(a[i++]));
  while (j < m) out.push(add(b[j++]));
  return out;
}

export function diffStats(oldText: string | null, newText: string): DiffStats {
  let added = 0;
  let removed = 0;
  for (const line of diffLines(oldText, newText)) {
    if (line.type === 'add') added++;
    else if (line.type === 'del') removed++;
  }
  return { added, removed };
}

export interface NumberedLine extends DiffLine {
  /** Line number in the old text, for context and removed lines. */
  oldNo: number | null;
  /** Line number in the new text, for context and added lines. */
  newNo: number | null;
}

/** Adds line numbers, counted within the changed region (agents do not say where in the file it is). */
export function numberLines(lines: DiffLine[]): NumberedLine[] {
  let oldNo = 0;
  let newNo = 0;
  return lines.map((line) => {
    if (line.type === 'ctx') return { ...line, oldNo: ++oldNo, newNo: ++newNo };
    if (line.type === 'del') return { ...line, oldNo: ++oldNo, newNo: null };
    return { ...line, oldNo: null, newNo: ++newNo };
  });
}
