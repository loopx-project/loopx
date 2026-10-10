/** Read-only Lark transport for the personal follow-through profile. */
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {FollowThroughError, digest, instant, object, text, sourceWindow,
  type FollowThroughConfig, type SourceWindow} from '../../control_plane/work_items/personal_follow_through/contract.ts';
const execute = promisify(execFile);
export type LarkReader = (args: string[]) => Promise<unknown>;
export async function readLark(args: string[]): Promise<unknown> {
  try {
    const {stdout} = await execute('lark-cli', args, {timeout: 30_000, maxBuffer: 2_000_000});
    return JSON.parse(stdout);
  } catch { throw new FollowThroughError('lark_read_failed_check_profile_and_permissions'); }
}
export async function captureLark(c: FollowThroughConfig, start: string, end: string,
  grantCurrent: () => Promise<boolean>, reader: LarkReader = readLark): Promise<SourceWindow> {
  if (!c.enabled) throw new FollowThroughError('profile_disabled');
  // Validate window before any subprocess/network action.
  sourceWindow({binding: c.binding, chat: c.chat_id, start, end, capturedAt: new Date().toISOString(), complete: true, messages: []}, c);
  const messages: SourceWindow['messages'] = [];
  const seenPages = new Set<string>();
  let page: string | null = null;
  for (let count = 0; count < 4; count++) {
    const args = ['--profile', c.lark_profile, 'im', '+chat-messages-list', '--as', 'bot',
      '--chat-id', c.chat_id, '--start', instant(start, 'start'), '--end', instant(end, 'end'),
      '--order', 'asc', '--page-size', '50', '--no-reactions', '--format', 'json'];
    if (page) args.push('--page-token', page);
    if (!await grantCurrent()) throw new FollowThroughError('profile_revoked');
    const envelope = object(await reader(args), 'lark_response');
    if (!await grantCurrent()) throw new FollowThroughError('profile_revoked');
    if (envelope.ok !== true || envelope.identity !== 'bot') throw new FollowThroughError('lark_identity_or_permission_failed');
    const data = object(envelope.data, 'lark_data');
    if (!Array.isArray(data.messages) || typeof data.has_more !== 'boolean') throw new FollowThroughError('lark_response_shape_unsupported');
    for (const value of data.messages) {
      const m = object(value, 'lark_message');
      if (m.chat_id !== undefined && m.chat_id !== c.chat_id) throw new FollowThroughError('lark_source_mismatch');
      if (m.deleted === true) continue;
      // Do not silently interpret attachments/cards as complete text evidence.
      if (m.msg_type !== 'text') throw new FollowThroughError('lark_message_type_requires_manual_review');
      const sender = object(m.sender, 'lark_sender');
      const body = text(m.content, 'message_content', 12000);
      const item = {id: text(m.message_id, 'message_id', 100),
        sender: text(sender.id, 'sender_id', 100), text: body,
        revision: digest({content: body, updated: m.update_time ?? m.create_time})};
      const duplicate = messages.find(v => v.id === item.id);
      if (duplicate && digest(duplicate) !== digest(item)) throw new FollowThroughError('source_changed_during_capture');
      if (!duplicate) messages.push(item);
      if (messages.length > 200) throw new FollowThroughError('source_window_capacity');
    }
    if (!data.has_more) return sourceWindow({binding: c.binding, chat: c.chat_id, start, end,
      capturedAt: new Date().toISOString(), complete: true, messages}, c);
    page = text(data.page_token, 'page_token', 2048);
    if (seenPages.has(page)) throw new FollowThroughError('lark_cursor_did_not_advance');
    seenPages.add(page);
  }
  throw new FollowThroughError('source_window_capacity_reduce_window');
}
