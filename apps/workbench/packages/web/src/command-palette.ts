/** The ⌘K palette's matching: pure, so the ranking can be tested without a DOM. */

export type PaletteGroup = 'commands' | 'sessions' | 'goals' | 'settings';
export const PALETTE_GROUPS: PaletteGroup[] = ['commands', 'sessions', 'goals', 'settings'];

export interface PaletteItem {
  id: string;
  group: PaletteGroup;
  label: string;
  /** Quieter second line: a project path, a Goal's status. */
  hint?: string;
  /** Extra words the query may hit without showing them. */
  keywords?: string;
  /** A Goal that waits on the person: shown with a mark and ranked first among equals. */
  needsYou?: boolean;
}

/** With no query the palette lists this many per group; a query shows this many in all. */
const IDLE_PER_GROUP = 5;
const MAX_RESULTS = 30;

const fold = (text: string) => text.toLowerCase().normalize('NFKC');

/** Lower is better; null when a term is missing. A label that starts with the term beats one that merely has it. */
function score(item: PaletteItem, terms: string[]): number | null {
  const label = fold(item.label);
  const rest = fold(`${item.hint ?? ''} ${item.keywords ?? ''}`);
  let total = 0;
  for (const term of terms) {
    const at = label.indexOf(term);
    if (at === 0) total += 0;
    else if (at > 0) total += /[\s/_\-·.]/.test(label[at - 1]) ? 1 : 2;
    else if (rest.includes(term)) total += 4;
    else return null;
  }
  return total;
}

export function filterPalette(items: PaletteItem[], query: string): PaletteItem[] {
  const terms = fold(query).split(/\s+/).filter(Boolean);
  if (!terms.length) {
    const seen = new Map<PaletteGroup, number>();
    return items.filter(item => {
      const n = seen.get(item.group) ?? 0;
      seen.set(item.group, n + 1);
      return n < IDLE_PER_GROUP || item.needsYou === true;
    });
  }
  return items
    .map((item, order) => ({ item, order, s: score(item, terms) }))
    .filter((row): row is { item: PaletteItem; order: number; s: number } => row.s !== null)
    .sort((a, b) => a.s - b.s || Number(b.item.needsYou === true) - Number(a.item.needsYou === true) || a.order - b.order)
    .slice(0, MAX_RESULTS)
    .map(row => row.item);
}

/** The ⌘K / Ctrl+K chord. Not claimed while a text field has it for its own use (none do today, but a code editor might). */
export const isPaletteChord = (event: Pick<KeyboardEvent, 'key' | 'metaKey' | 'ctrlKey' | 'altKey' | 'shiftKey'>): boolean =>
  (event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 'k';
