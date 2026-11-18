/** Only consecutive, valid bracket prefixes are tags. Invalid prefixes stay in the title. */
export function validTag(value: string): boolean {
  return [...value].length >= 1 && [...value].length <= 20 && !/[\[\]【】]/u.test(value);
}

export function parseTitleTags(title: string): { title: string; tags: string[] } {
  let rest = title.trimStart();
  const tags = new Map<string, string>();
  for (;;) {
    const match = /^(?:\[([^\[\]【】]*)\]|【([^\[\]【】]*)】)\s*/u.exec(rest);
    if (!match) break;
    const tag = (match[1] ?? match[2]).trim();
    if (!validTag(tag)) break;
    if (!tags.has(tag.toLowerCase())) tags.set(tag.toLowerCase(), tag);
    rest = rest.slice(match[0].length);
  }
  return { title: tags.size ? rest.trim() || title : title, tags: [...tags.values()] };
}

/** Count threads, not repeated tags; preserve the first spelling across the board. */
export function collectTags(threads: readonly { title: string; archived: boolean; createdAt?: string }[]) {
  const tags = new Map<string, { key: string; label: string; count: number }>();
  for (const thread of [...threads].sort((a, b) => (a.createdAt ?? '').localeCompare(b.createdAt ?? ''))) {
    if (thread.archived) continue;
    for (const label of parseTitleTags(thread.title).tags) {
      const key = label.toLowerCase();
      const existing = tags.get(key);
      if (existing) existing.count++;
      else tags.set(key, { key, label, count: 1 });
    }
  }
  return [...tags.values()].sort((a, b) => b.count - a.count || a.key.localeCompare(b.key));
}
