export interface MeetingReference {
  event: 'ended' | 'note-generated' | 'minutes-generated';
  meetingId?: string;
  calendarEventId?: string;
  noteId?: string;
  noteDocToken?: string;
  minuteToken?: string;
  title?: string;
  startedAt?: string;
  endedAt?: string;
}
export interface SignalMaterial {
  state: 'unread' | 'not-ready' | 'ready' | 'empty' | 'failed';
  kind?: 'note' | 'minutes';
  checkedAt?: string;
  error?: string;
  partial?: boolean;
}
export interface SignalMaterialRead {
  material: SignalMaterial;
  content: string | null;
}
/** Exit status is separate from JSON: the CLI can return useful structured partial failures. */
export interface MaterialCommandResult { value: unknown; failed: boolean }
export type MaterialCommand = (args: string[]) => Promise<MaterialCommandResult>;

const object = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const MAX_CONTENT = 16_000;
const MAX_TODOS = 100;
const ERRORS = new Set(['cli_missing', 'cli_timeout', 'cli_failed', 'cli_unexpected_output', 'lark_not_logged_in', 'lark_configuration_changed', 'lark_identity_changed', 'lark_disabled', 'personal_not_configured', 'material_permission_denied', 'material_not_found', 'material_unavailable']);

/** Keep even large numeric IDs as strings; never let an option or URL become a CLI read target. */
export function resourceId(value: unknown, kind?: 'minutes'): string | undefined {
  return typeof value === 'string' && value.length <= 200 && (kind === 'minutes' ? /^[a-z0-9]+$/ : /^[A-Za-z0-9][A-Za-z0-9_-]*$/).test(value) ? value : undefined;
}

/** A title is never a join key. Note IDs and their document tokens must remain a pair. */
export function mergeMeetingReferences(reference: MeetingReference, related: MeetingReference[]): MeetingReference {
  const result = { ...reference };
  if (!resourceId(reference.meetingId)) return result;
  for (const other of related) {
    if (other.meetingId !== reference.meetingId) continue;
    result.calendarEventId ??= resourceId(other.calendarEventId);
    result.minuteToken ??= resourceId(other.minuteToken, 'minutes');
    if (!result.noteId && !result.noteDocToken) {
      result.noteId = resourceId(other.noteId);
      result.noteDocToken = resourceId(other.noteDocToken);
    } else if (result.noteId && other.noteId === result.noteId) result.noteDocToken ??= resourceId(other.noteDocToken);
    else if (!result.noteId && result.noteDocToken === other.noteDocToken) result.noteId = resourceId(other.noteId);
    result.title ??= other.title;
    result.startedAt ??= other.startedAt;
    result.endedAt ??= other.endedAt;
  }
  // Undefined properties must not bloat lightweight event payloads or their fingerprints.
  return Object.fromEntries(Object.entries(result).filter(([, value]) => value !== undefined)) as unknown as MeetingReference;
}

export function materialError(error: unknown): string {
  const message = error instanceof Error ? error.message : '';
  return ERRORS.has(message) ? message : 'cli_failed';
}
function failure(result: MaterialCommandResult, row: Record<string, unknown> = {}): string {
  const root = object(result.value);
  const code = object(row.error).code ?? row.code ?? object(root.error).code ?? root.code;
  if (code === 121005 || code === '121005') return 'material_permission_denied';
  if (code === 2091004 || code === '2091004') return 'material_not_found';
  return 'cli_failed';
}
function data(result: MaterialCommandResult): Record<string, unknown> {
  const root = object(result.value);
  if (root.identity !== undefined && root.identity !== 'user') throw new Error('lark_identity_changed');
  if (!root.data || typeof root.data !== 'object' || Array.isArray(root.data)) throw new Error(result.failed || root.ok === false ? failure(result) : 'cli_unexpected_output');
  return object(root.data);
}
function item(result: MaterialCommandResult, field: string, idField: string, id: string): Record<string, unknown> {
  const list = data(result)[field];
  if (!Array.isArray(list)) throw new Error('cli_unexpected_output');
  const found = list.map(object).find(row => row[idField] === id);
  if (!found) throw new Error('cli_unexpected_output');
  return found;
}
function bounded(parts: string[]): { content: string | null; partial: boolean } {
  // Only rendered artifacts cross this boundary, never CLI diagnostics, callback tokens or sidecars.
  const text = parts.join('\n\n').replace(/-----BEGIN (?:[A-Z ]*PRIVATE KEY)-----[\s\S]*?-----END (?:[A-Z ]*PRIVATE KEY)-----|\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[A-Z0-9]{16})/g, '[redacted]').trim();
  const characters = [...text];
  return { content: characters.length ? characters.slice(0, MAX_CONTENT).join('') : null, partial: characters.length > MAX_CONTENT };
}

/** Read existing summary/TODO artifacts only. No transcript, media, bot fallback or permission requests. */
export async function readMeetingMaterial(reference: MeetingReference, command: MaterialCommand, checkedAt: string): Promise<SignalMaterialRead> {
  let kind: SignalMaterial['kind'];
  const result = (state: SignalMaterial['state'], content: string | null = null, extra: Partial<SignalMaterial> = {}): SignalMaterialRead => ({ material: { state, ...(kind ? { kind } : {}), checkedAt, ...extra }, content });
  const read = async (args: string[]) => command([...args, '--as', 'user']);
  try {
    let noteId = resourceId(reference.noteId), docToken = resourceId(reference.noteDocToken), minuteToken = resourceId(reference.minuteToken, 'minutes');
    const meetingId = resourceId(reference.meetingId);
    if (!noteId && !docToken && !minuteToken && meetingId) {
      const response = await read(['vc', '+detail', '--meeting-ids', meetingId, '--format', 'json']);
      const meeting = item(response, 'meetings', 'meeting_id', meetingId);
      if (meeting.error) throw new Error(failure(response, meeting));
      noteId = resourceId(meeting.note_id);
      minuteToken = resourceId(meeting.minute_token, 'minutes');
    }
    // An explicit minutes event follows its own artifact, rather than mixing two AI summaries.
    if (minuteToken && (reference.event === 'minutes-generated' || (!noteId && !docToken))) {
      kind = 'minutes';
      const response = await read(['minutes', '+detail', '--minute-tokens', minuteToken, '--summary', '--todo', '--format', 'json']);
      const minute = item(response, 'minutes', 'minute_token', minuteToken);
      if (minute.status === 'processing' || minute.retryable === true) return result('not-ready');
      const artifacts = object(minute.artifacts);
      if (typeof artifacts.summary !== 'string' || !Array.isArray(artifacts.todos)) throw new Error(minute.error || response.failed ? failure(response, minute) : 'cli_unexpected_output');
      const todos = artifacts.todos.slice(0, MAX_TODOS).map(object).map(todo => todo.content).filter((text): text is string => typeof text === 'string' && Boolean(text.trim()));
      const body = bounded([artifacts.summary, ...(todos.length ? ['## TODO\n' + todos.map(text => `- ${text}`).join('\n')] : [])]);
      const partial = body.partial || artifacts.todos.length > MAX_TODOS || response.failed || Boolean(minute.error);
      return result(body.content ? 'ready' : 'empty', body.content, partial ? { partial: true } : {});
    }
    if (!noteId && !docToken) return result('not-ready');
    kind = 'note';
    if (noteId) {
      const response = await read(['note', '+detail', '--note-id', noteId, '--format', 'json']);
      const note = object(data(response).note);
      if (note.note_id !== noteId) throw new Error('cli_unexpected_output');
      if (response.failed || object(response.value).ok === false) throw new Error(failure(response));
      // Do not read verbatim_doc_token or shared_doc_tokens, even for a unified Note.
      docToken = resourceId(note.note_doc_token);
      if (!docToken) return result('not-ready');
    }
    if (!docToken) return result('not-ready');
    const fragments: string[] = [];
    for (const keyword of ['待办', 'TODO']) {
      const response = await read(['docs', '+fetch', '--doc', docToken, '--scope', 'keyword', '--keyword', keyword, '--context-before', '1', '--context-after', '2', '--doc-format', 'markdown']);
      const document = object(data(response).document);
      if (typeof document.content !== 'string') throw new Error('cli_unexpected_output');
      if (response.failed || object(response.value).ok === false) throw new Error(failure(response));
      if (document.content.trim()) { fragments.push(document.content); break; }
    }
    const body = bounded(fragments);
    // A keyword excerpt, including an empty excerpt, is never evidence about the whole Note.
    return result(body.content ? 'ready' : 'empty', body.content, { partial: true });
  } catch (error) {
    return result('failed', null, { error: materialError(error) });
  }
}
