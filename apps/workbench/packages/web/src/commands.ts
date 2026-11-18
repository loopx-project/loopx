/**
 * What the slash menu shows for the text in the composer. Pure, so it can be tested directly.
 * The menu mixes two things: actions the hub performs itself, and commands sent to the agent.
 */
import type { SlashCommand } from './api';
import { t } from './i18n';

/** Something the hub does itself when picked, such as opening the model picker. */
export interface HubAction {
  id: string;
  label: string;
  hint: string;
  disabled?: boolean;
  /** Extra words that find this action, e.g. the English name. Lower case. */
  keywords: string[];
}

export type MenuRow =
  | { type: 'heading'; label: string }
  | { type: 'action'; action: HubAction }
  | { type: 'command'; command: SlashCommand };

const KINDS: Array<NonNullable<SlashCommand['kind']>> = ['command', 'prompt', 'skill'];

/** The text after the slash, lower-cased, or null when the menu should be closed. */
export function slashQuery(text: string): string | null {
  return /^\/\S*$/.test(text) ? text.slice(1).toLowerCase() : null;
}

function rankCommand(cmd: SlashCommand, query: string): number {
  const name = cmd.name.toLowerCase();
  if (name.startsWith(query)) return 0;
  if (name.includes(query)) return 1;
  // One letter matches almost every description, so descriptions join in from two letters on.
  if (query.length >= 2 && cmd.description.toLowerCase().includes(query)) return 2;
  return -1;
}

/**
 * Rows for the menu, with headings. `hidden` lists agent commands the hub replaces with its own
 * control (picking a model through the agent's command would leave the hub showing the old one).
 */
export function buildMenu(text: string, actions: HubAction[], commands: SlashCommand[], hidden: string[] = []): MenuRow[] {
  const query = slashQuery(text);
  if (query === null) return [];
  return searchCommands(query, actions, commands, hidden);
}

/** The same catalog can be searched without typing a slash. */
export function searchCommands(query: string, actions: HubAction[], commands: SlashCommand[], hidden: string[] = []): MenuRow[] {
  query = query.trim().replace(/^\//, '').toLowerCase();

  const rows: MenuRow[] = [];
  const matchedActions = actions.filter(
    (a) => query === '' || a.label.toLowerCase().includes(query) || a.keywords.some((k) => k.startsWith(query)),
  );
  if (matchedActions.length) {
    rows.push({ type: 'heading', label: t('commands.actions') });
    for (const action of matchedActions) rows.push({ type: 'action', action });
  }

  const ranked = commands
    .map((c) => ({ ...c, name: c.name.replace(/^\//, '') }))
    .filter((c) => !hidden.includes(c.name))
    .map((command) => ({ command, rank: rankCommand(command, query) }))
    .filter((r) => r.rank >= 0)
    .sort((a, b) => a.rank - b.rank); // stable: the agent's own order within a rank

  const known = ranked.some((r) => r.command.kind);
  if (!known) {
    // The agent does not say which entries are skills, so they are not split into made-up groups.
    if (ranked.length) rows.push({ type: 'heading', label: t('commands.commandsAndSkills') });
    for (const r of ranked) rows.push({ type: 'command', command: r.command });
    return rows;
  }
  for (const kind of KINDS) {
    const group = ranked.filter((r) => (r.command.kind ?? 'command') === kind);
    if (group.length === 0) continue;
    rows.push({ type: 'heading', label: t(`commands.kind.${kind}`) });
    for (const r of group) rows.push({ type: 'command', command: r.command });
  }
  return rows;
}

/** Add a command to the existing words, replacing only a known command prefix. */
export function commandDraft(name: string, text: string, commands: SlashCommand[]): string {
  const prefix = /^\/(\S*)(?:\s+|$)/.exec(text);
  const body = prefix && (!prefix[1] || commands.some(c => c.name.replace(/^\//, '') === prefix[1]))
    ? text.slice(prefix[0].length) : text;
  return `/${name.replace(/^\//, '')} ${body}`;
}
