const LABEL_SEPARATORS = /[：:，,（(…　]/;
const MAX_LABEL = 24;

/**
 * One-line label for dense rows. Goal titles are whole objectives, so a row
 * takes the clause before the first separator when that clause is short
 * enough to read, and otherwise keeps the title for CSS to ellipsize. The full
 * title stays in the tooltip and in the Goal's own header.
 */
export function goalShortLabel(goal: { title: string }): string {
  const raw = goal.title.trim();
  if (raw.length <= MAX_LABEL) return raw;
  const head = raw.split(LABEL_SEPARATORS)[0]?.trim() ?? "";
  if (head.length >= 4 && head.length <= MAX_LABEL) return head;
  return raw;
}
