/**
 * How each agent words a tool call, an approval request and a failure, read down to the plain facts
 * the thread brief reports. Every adapter's quirks stop here; the brief itself never looks at an
 * agent's own field names.
 */

const obj = (value: unknown): Record<string, unknown> => (value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {});
const str = (value: unknown): string => (typeof value === 'string' ? value : '');

/** Tool kinds that change files, as every adapter reports them. */
const WRITE_KINDS = new Set(['edit', 'write', 'create', 'delete', 'move']);

export function isWriteTool(data: Record<string, unknown>): boolean {
  return WRITE_KINDS.has(str(data.kind));
}

/** Keys under which agents put the file a call touches. */
const PATH_KEYS = ['file_path', 'filePath', 'path', 'notebook_path'];

/**
 * The files a tool call or approval request names: a path in its input (Claude, Pi), the list of
 * changes (Codex), or the diffs and locations it carries (ACP). Empty when the agent says nothing.
 */
export function namedPaths(data: Record<string, unknown>): string[] {
  const found = new Set<string>();
  const add = (value: unknown) => { const path = str(value).trim(); if (path) found.add(path); };
  const scan = (source: Record<string, unknown>) => {
    for (const key of PATH_KEYS) add(source[key]);
    if (Array.isArray(source.changes)) for (const change of source.changes) add(obj(change).path);
    if (Array.isArray(source.locations)) for (const place of source.locations) add(obj(place).path);
    if (Array.isArray(source.content)) for (const item of source.content) if (obj(item).type === 'diff') add(obj(item).path);
  };
  scan(data);
  scan(obj(data.input));
  scan(obj(data.rawInput));
  return [...found];
}

/** What a pending approval asks to do, in the terms the person cares about. */
export interface ApprovalFacts {
  action: 'run' | 'edit' | 'delete' | 'other';
  /** The command to run, first line only. */
  command: string | null;
  paths: string[];
  /** What the agent called the step, when there is nothing better to say. */
  title: string | null;
}

/** Reads an `approval.request` event's tool call (Claude and ACP agents, Codex, zcode). */
export function approvalFacts(title: unknown, toolCall: unknown): ApprovalFacts {
  const call = obj(toolCall);
  const raw = obj(call.rawInput);
  const rawCommand = raw.command ?? call.command;
  const command = (Array.isArray(rawCommand) ? rawCommand.map(String).join(' ') : str(rawCommand)).split('\n')[0].trim() || null;
  const paths = namedPaths(call);
  const kind = str(call.kind);
  let action: ApprovalFacts['action'] = 'other';
  if (kind === 'delete') action = 'delete';
  else if (kind === 'execute' || kind === 'command' || (!kind && command)) action = 'run';
  else if (WRITE_KINDS.has(kind) || kind === 'fileChange' || (!kind && paths.length)) action = 'edit';
  const named = str(call.title) || str(title);
  return { action, command, paths, title: named.split('\n')[0].trim() || null };
}

/**
 * A failure's message with what varies between attempts blanked out, so the same error twice in a
 * row compares equal: ids, counts, clock times and request numbers differ, the wording does not.
 */
export function failureSignature(message: string): string {
  return message
    .replace(/^(Internal error|Error|API Error):\s*/i, '')
    .split('\n')[0]
    .toLowerCase()
    .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g, '#')
    .replace(/\b[0-9a-f]{16,}\b/g, '#')
    .replace(/\d+/g, '#')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 120);
}
