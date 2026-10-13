/** Owner-reviewed personal work. Source/model text never carries mutation authority. */
import {parseExactGoalRef} from '../../goals/goal_instance_identity.ts';
import type {JsonObject} from '../../effect_program.ts';
import {canonicalAuthoritySha256 as digest, requireAuthorityStoreId} from '../../coordination/authority_store_codec.ts';

export class FollowThroughError extends Error {
  readonly code: string;
  constructor(code: string) { super(code); this.code = code; this.name = 'FollowThroughError'; }
}
export interface SourceMessage {
  id: string; revision: string; sender: string; text: string;
}
export interface SourceWindow {
  binding: string; chat: string; start: string; end: string;
  capturedAt: string; messages: SourceMessage[]; complete: boolean;
}
export interface FollowThroughConfig {
  schema_version: 'personal_follow_through_config_v0'; enabled: boolean;
  runtime_root: string; goal_id: string; goal_instance_id: string; registry_path: string; owner_id: string;
  binding: string; chat_id: string; lark_profile: string;
  model: {endpoint: string; name: string; key_env: string};
}
export interface Candidate {
  kind: 'create' | 'amend'; title: string; responsible_id: string;
  source_ids: string[]; target_todo_id: string | null;
  due_at?: string | null; due_basis?: string | null;
}
export interface ReviewPacket {
  schema_version: 'personal_follow_through_review_v0';
  config_digest: string; registry_digest: string; source: SourceWindow; candidate: Candidate;
  expected_provider_revision: string; operation_id: string; todo_id: string;
  note: string; observed_at: string;
}
export {digest};
export function object(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new FollowThroughError(`invalid_${label}`);
  return value as Record<string, unknown>;
}
export function text(value: unknown, label: string, limit = 4000): string {
  if (typeof value !== 'string' || !value.trim() || value.length > limit || /[\u0000-\u0008\u000b-\u001f]/u.test(value)) throw new FollowThroughError(`invalid_${label}`);
  return value;
}
export function instant(value: unknown, label: string): string {
  const s = text(value, label, 64);
  if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,3})?(?:Z|[+-]\d\d:\d\d)$/.test(s) || !Number.isFinite(Date.parse(s))) throw new FollowThroughError(`invalid_${label}`);
  if (new Date(s.slice(0, 10) + 'T00:00:00Z').toISOString().slice(0, 10) !== s.slice(0, 10)) throw new FollowThroughError(`invalid_${label}`);
  return s;
}
export function config(value: unknown): FollowThroughConfig {
  const r = object(value, 'config');
  if (r.schema_version !== 'personal_follow_through_config_v0' || typeof r.enabled !== 'boolean') throw new FollowThroughError('invalid_config_version');
  if (parseExactGoalRef(r).kind !== 'parsed') throw new FollowThroughError('exact_goal_identity_required');
  const model = object(r.model, 'model');
  const endpoint = new URL(text(model.endpoint, 'model_endpoint'));
  if (endpoint.protocol !== 'https:' && !(endpoint.protocol === 'http:' && ['127.0.0.1', '[::1]', 'localhost'].includes(endpoint.hostname))) throw new FollowThroughError('insecure_model_endpoint');
  if (endpoint.username || endpoint.password || endpoint.search || endpoint.hash) throw new FollowThroughError('invalid_model_endpoint');
  const owner = text(r.owner_id, 'owner_id', 100), chat = text(r.chat_id, 'chat_id', 100);
  if (!/^ou_[A-Za-z0-9_-]+$/.test(owner) || !/^oc_[A-Za-z0-9_-]+$/.test(chat)) throw new FollowThroughError('invalid_lark_identity');
  const env = text(model.key_env, 'key_env', 100);
  if (!/^[A-Z][A-Z0-9_]*$/.test(env)) throw new FollowThroughError('invalid_key_env');
  return {schema_version: 'personal_follow_through_config_v0', enabled: r.enabled,
    runtime_root: text(r.runtime_root, 'runtime_root'), registry_path: text(r.registry_path, 'registry_path'), goal_instance_id: String(r.goal_instance_id), goal_id: requireAuthorityStoreId(r.goal_id, 'goal id'),
    owner_id: owner, binding: requireAuthorityStoreId(r.binding, 'binding'), chat_id: chat,
    lark_profile: text(r.lark_profile, 'lark_profile', 100),
    model: {endpoint: endpoint.href, name: text(model.name, 'model_name', 100), key_env: env}};
}
export function candidate(value: unknown, source: SourceWindow, owner: string): Candidate {
  const r = object(value, 'candidate');
  if (r.kind !== 'create' && r.kind !== 'amend') throw new FollowThroughError('unsupported_candidate_kind');
  if (r.responsible_id !== owner) throw new FollowThroughError('responsibility_requires_clarification');
  if (!Array.isArray(r.source_ids) || !r.source_ids.length || r.source_ids.length > 20) throw new FollowThroughError('invalid_source_ids');
  const ids = [...new Set(r.source_ids.map(v => text(v, 'source_id', 100)))].sort();
  if (ids.some(id => !source.messages.some(m => m.id === id))) throw new FollowThroughError('unknown_evidence');
  // A third-party message may amend a promise only together with its owner's evidence.
  if (!source.messages.some(m => ids.includes(m.id) && m.sender === owner)) throw new FollowThroughError('missing_owner_evidence');
  const title = text(r.title, 'title', 1000);
  if (title.includes('\n')) throw new FollowThroughError('invalid_title');
  if (Object.hasOwn(r, 'due_at') !== Object.hasOwn(r, 'due_basis')) throw new FollowThroughError('due_date_requires_basis');
  const due = !Object.hasOwn(r, 'due_at') ? undefined : r.due_at === null ? null : instant(r.due_at, 'due_at');
  const basis = !Object.hasOwn(r, 'due_basis') ? undefined : r.due_basis === null ? null : text(r.due_basis, 'due_basis', 1000);
  if ((due === null) !== (basis === null)) throw new FollowThroughError('due_date_requires_basis');
  const target = r.target_todo_id === null ? null : requireAuthorityStoreId(r.target_todo_id, 'target Todo');
  if ((r.kind === 'create') !== (target === null)) throw new FollowThroughError('invalid_candidate_target');
  return {kind: r.kind, title, responsible_id: owner, source_ids: ids, target_todo_id: target, ...(due === undefined ? {} : {due_at: due, due_basis: basis!})};
}
export function sourceWindow(value: unknown, c: FollowThroughConfig): SourceWindow {
  const r = object(value, 'source_window');
  if (r.binding !== c.binding || r.chat !== c.chat_id || typeof r.complete !== 'boolean' || !Array.isArray(r.messages) || r.messages.length > 200) throw new FollowThroughError('invalid_source_scope');
  const start = instant(r.start, 'start'), end = instant(r.end, 'end');
  if (Date.parse(start) >= Date.parse(end) || Date.parse(end) - Date.parse(start) > 7 * 86400000) throw new FollowThroughError('invalid_source_window');
  const messages = r.messages.map(v => {const m = object(v, 'message'); return {
    id: text(m.id, 'message_id', 100), revision: text(m.revision, 'revision', 100),
    sender: text(m.sender, 'sender', 100), text: text(m.text, 'message_text', 12000)};});
  if (new Set(messages.map(m => m.id)).size !== messages.length) throw new FollowThroughError('duplicate_source_identity');
  return {binding: c.binding, chat: c.chat_id, start, end, capturedAt: instant(r.capturedAt, 'capturedAt'), messages, complete: r.complete};
}
export function buildReview(c: FollowThroughConfig, source: SourceWindow, raw: unknown,
  head: {revision: string; todos: JsonObject[]; registry_digest: string}, now = new Date().toISOString()): ReviewPacket {
  if (!c.enabled) throw new FollowThroughError('profile_disabled');
  source = sourceWindow(source, c);
  if (!source.complete) throw new FollowThroughError('source_window_incomplete');
  const proposed = candidate(raw, source, c.owner_id);
  const selected = source.messages.filter(m => proposed.source_ids.includes(m.id)).map(m => ({id: m.id, revision: m.revision})).sort((a,b) => a.id.localeCompare(b.id));
  const identity = digest({binding: c.binding, chat: c.chat_id, goal: c.goal_id, instance: c.goal_instance_id, sources: selected, kind: proposed.kind, target: proposed.target_todo_id});
  const stableSourceIdentity = digest({binding: c.binding, chat: c.chat_id, goal: c.goal_id, instance: c.goal_instance_id, sources: selected.map(s => s.id)});
  const todoId = proposed.target_todo_id ?? `personal-${stableSourceIdentity.slice(0, 24)}`;
  const prior = head.todos.find(t => t.todo_id === todoId);
  if (proposed.kind === 'create' && prior) throw new FollowThroughError('source_already_tracked_use_amend');
  if (proposed.kind === 'amend' && (!prior || prior.role !== 'user' || prior.status !== 'open' || prior.archive_state !== 'active')) throw new FollowThroughError('target_not_open_user_todo');
  // Dates remain explicit review metadata; they do not install a reminder/scheduler.
  const metadata = JSON.stringify({source_binding: c.binding, sources: selected, due_at: proposed.due_at, due_basis: proposed.due_basis});
  const note = proposed.kind === 'amend' && typeof prior?.note === 'string' ? `${prior.note}\n${metadata}` : metadata;
  if (note.length > 8000) throw new FollowThroughError('review_note_capacity');
  return {schema_version: 'personal_follow_through_review_v0', config_digest: digest(c), registry_digest: head.registry_digest, source,
    candidate: proposed, expected_provider_revision: head.revision, operation_id: `personal-${identity}`,
    todo_id: todoId, note, observed_at: instant(now, 'observed_at')};
}
