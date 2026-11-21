/** Only the query before a collapsed caret belongs to the mention; preserve the rest of the draft. */
export function mentionAt(text: string, start: number, end = start) {
  if (start !== end || start < 0 || start > text.length) return null;
  const match = /(?:^|\s)@([^\s@]*)$/.exec(text.slice(0, start));
  if (!match) return null;
  return {
    start: start - match[1].length - 1,
    end: start,
    query: match[1],
  };
}
