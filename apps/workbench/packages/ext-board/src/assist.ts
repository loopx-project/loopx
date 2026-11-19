import type { Locale } from '@calli/extension-api/web';
import { activeAt, COLUMN_IDS, type BoardCard, type BoardColumn, type ColumnId } from './columns';
import { t } from './i18n';
import { machineName, reasonOf } from './reason';
import { parseTitleTags } from './tags';

/** Past this many cards the summary leaves the rest out, so the assistant's message stays readable. */
export const SUMMARY_LIMIT = 80;
const NOTE_LIMIT = 200;
/** The columns that can need the person, most urgent first; done ones are settled. */
const ORDER = ['waiting', 'review', 'doing'] as const satisfies readonly ColumnId[];

export interface Picked {
  cards: BoardCard[];
  /** Cards left out, by the column they sit in. */
  omitted: Partial<Record<ColumnId, number>>;
  /** Done cards, which are never listed. */
  doneCount: number;
  /** Backlog cards, which are never listed: the person put them off. */
  laterCount: number;
}

/** Up to `limit` cards: waiting, then to-review (latest first), then in progress; the rest are counted. */
export function pickCards(columns: readonly BoardColumn[], limit = SUMMARY_LIMIT): Picked {
  const byId = new Map(columns.map((column) => [column.id, column]));
  const cards: BoardCard[] = [];
  const omitted: Partial<Record<ColumnId, number>> = {};
  for (const id of ORDER) {
    const column = byId.get(id);
    if (!column) continue;
    // Columns hold their cards newest first, but only the unfolded ones; `older` has the rest.
    const all = [...column.cards, ...column.older.filter((card) => !column.cards.includes(card))]
      .sort((a, b) => Number(b.thread.pinned) - Number(a.thread.pinned) || activeAt(b) - activeAt(a) || a.thread.id.localeCompare(b.thread.id));
    const room = Math.max(0, limit - cards.length);
    cards.push(...all.slice(0, room));
    if (all.length > room) omitted[id] = all.length - room;
  }
  return { cards, omitted, doneCount: byId.get('done')?.total ?? 0, laterCount: byId.get('backlog')?.total ?? 0 };
}

/** Link text must not close the link early. */
function linkText(text: string): string {
  return text.replace(/[\\[\]]/g, (char) => `\\${char}`).replace(/\s+/g, ' ');
}

function oneLine(text: string, limit: number): string {
  const flat = text.replace(/\s+/g, ' ').trim();
  return [...flat].length > limit ? `${[...flat].slice(0, limit).join('')}…` : flat;
}

export function elapsed(locale: Locale, ms: number): string {
  const minutes = Math.max(0, Math.floor(ms / 60_000));
  if (minutes < 60) return t(locale, 'durMinutes', minutes);
  const hours = Math.floor(minutes / 60);
  return hours < 48 ? t(locale, 'durHours', hours) : t(locale, 'durDays', Math.floor(hours / 24));
}

export interface SummaryContext {
  locale: Locale;
  now: number;
  threadLink: (id: string) => string;
  projectLabel: (key: string) => string;
  projectOf: (card: BoardCard) => string;
}

function line(card: BoardCard, index: number, ctx: SummaryContext): string {
  const { locale } = ctx;
  const { thread } = card;
  const title = parseTitleTags(thread.title).title || t(locale, 'untitled');
  const reason = reasonOf(card, locale, ctx.now);
  const facts = [t(locale, card.column), reason.text];
  if (card.reason.kind !== 'running' && reason.at && Number.isFinite(Date.parse(reason.at))) facts.push(t(locale, 'summaryStopped', elapsed(locale, ctx.now - Date.parse(reason.at))));
  facts.push(ctx.projectLabel(ctx.projectOf(card)));
  if (thread.machine) facts.push(machineName(thread.machine, locale));
  const link = ctx.threadLink(thread.id).replace(/\)/g, '%29').replace(/\s/g, '%20');
  const note = thread.report?.note.trim();
  return `${index + 1}. [${linkText(title)}](${link}) — ${facts.join(' · ')}${note ? `\n   > ${oneLine(note, NOTE_LIMIT)}` : ''}`;
}

/** The message handed to the assistant: what is on the board, and the three things to answer. */
export function buildSummary(columns: readonly BoardColumn[], ctx: SummaryContext, limit = SUMMARY_LIMIT): string {
  const { locale } = ctx;
  const picked = pickCards(columns, limit);
  const omittedTotal = COLUMN_IDS.reduce((sum, id) => sum + (picked.omitted[id] ?? 0), 0);
  const out = [t(locale, 'askIntro', picked.cards.length), '', ...picked.cards.map((card, index) => line(card, index, ctx))];
  if (omittedTotal > 0) {
    const parts = ORDER.filter((id) => picked.omitted[id]).map((id) => t(locale, 'askOmittedIn', t(locale, id), picked.omitted[id] ?? 0));
    out.push('', t(locale, 'askOmitted', omittedTotal, parts.join(locale === 'en' ? ', ' : '、')));
  }
  if (picked.laterCount > 0) out.push('', t(locale, 'askLater', picked.laterCount));
  out.push('', t(locale, 'askQuestions'));
  return out.join('\n');
}
