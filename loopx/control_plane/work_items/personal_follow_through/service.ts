import {createHash} from 'node:crypto';
import {registryAuthoritySourceCheck} from '../../coordination/authority_source.ts';
import {parseExactGoalRef} from '../../goals/goal_instance_identity.ts';
import {open, lstat, realpath} from 'node:fs/promises';
import {constants} from 'node:fs';
import {captureLark, type LarkReader} from '../../../extensions/lark/personal_follow_through.ts';
import {isAbsolute} from 'node:path';
import type {JsonObject} from '../../effect_program.ts';
import {withCanonicalWriter} from '../../coordination/local_authority_write.ts';
import {openLocalAuthorityStore} from '../../coordination/local_authority_provider.ts';
import {executeCoordinationTodoCreate} from '../../coordination/todo_create.ts';
import {executeCoordinationTodoUpdate} from '../../coordination/todo_update.ts';
import {canonicalTodoCollection} from '../../coordination/local_authority_read.ts';
import {TODO_DOMAIN_ITEM_SCHEMA} from '../../coordination/coordination_state_contract.ts';
import {FollowThroughError, config, digest, object, buildReview, type ReviewPacket, type FollowThroughConfig} from './contract.ts';

async function readPrivateDocument(path: string) {
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const st = await handle.stat();
    if (!st.isFile() || (process.platform !== 'win32' && ((st.mode & 0o077) !== 0 || st.uid !== process.getuid?.())) || st.size > 2_000_000) throw new FollowThroughError('private_file_required');
    const raw = await handle.readFile('utf8');
    if (Buffer.byteLength(raw) > 2_000_000) throw new FollowThroughError('private_file_capacity');
    return {value: JSON.parse(raw) as unknown, sha256: createHash('sha256').update(raw).digest('hex')};
  } finally {await handle.close();}
}
export async function readPrivateJson(path: string): Promise<unknown> {return (await readPrivateDocument(path)).value;}
export async function loadConfig(path: string): Promise<FollowThroughConfig> {
  const c = config(await readPrivateJson(path));
  if (!isAbsolute(c.runtime_root)) throw new FollowThroughError('absolute_runtime_root_required');
  // This profile is owner-local. Never adopt a shared permissive runtime silently.
  const st = await lstat(c.runtime_root);
  if (!st.isDirectory() || st.isSymbolicLink() || (process.platform !== 'win32' && ((st.mode & 0o077) !== 0 || st.uid !== process.getuid?.()))) throw new FollowThroughError('private_runtime_required');
  return c;
}
/** Narrow admission adapter: legacy object registry, exact instance, active owner-local Goal.
 * Strict registry envelopes and registered-Agent Goals await their typed registry owner. */
async function registryAdmission(c: FollowThroughConfig) {
  if (!isAbsolute(c.registry_path)) throw new FollowThroughError('absolute_registry_required');
  const document = await readPrivateDocument(c.registry_path);
  const registry = object(document.value, 'supported_object_registry');
  if (registry.schema_version !== '0.1' || registry.registry_role !== 'project-local' ||
      typeof registry.common_runtime_root !== 'string' || !isAbsolute(registry.common_runtime_root) ||
      await realpath(registry.common_runtime_root) !== await realpath(c.runtime_root) || !Array.isArray(registry.goals)) throw new FollowThroughError('unsupported_registry_scope');
  const goals = registry.goals.map(v => object(v, 'registered_goal')).filter(g => g.id === c.goal_id);
  if (goals.length !== 1) throw new FollowThroughError('goal_not_uniquely_registered');
  const goal = goals[0];
  if (parseExactGoalRef({goal_id: goal.id, goal_instance_id: goal.goal_instance_id}).kind !== 'parsed' || goal.goal_instance_id !== c.goal_instance_id) throw new FollowThroughError('goal_instance_changed');
  // Deliberately admit only an explicit active state, avoiding a second default/legacy reducer.
  const activation = goal.activation == null ? {} : object(goal.activation, 'activation');
  const state = goal.activation_state ?? activation.state;
  if (state !== 'active' || (activation.schema_version != null && activation.schema_version !== 'loopx_goal_activation_v1')) throw new FollowThroughError('explicit_active_goal_required');
  const containers = [goal, goal.coordination == null ? {} : object(goal.coordination, 'coordination'), goal.spawn_policy == null ? {} : object(goal.spawn_policy, 'spawn_policy')];
  for (const entry of containers) {
    if (entry.registered_agents != null && (!Array.isArray(entry.registered_agents) || entry.registered_agents.length !== 0)) throw new FollowThroughError('registered_agent_goal_unsupported');
  }
  return {digest: document.sha256, registeredAgents: [] as string[],
    current: registryAuthoritySourceCheck({registry_source: {path: c.registry_path, sha256: document.sha256}}, true)};
}
export async function profileCurrent(path: string, original: FollowThroughConfig, registryDigest?: string): Promise<boolean> {
  try { const current = await loadConfig(path); if (!current.enabled || digest(current) !== digest(original)) return false; const authority = await registryAdmission(current); return (registryDigest === undefined || authority.digest === registryDigest) && await authority.current(); }
  catch { return false; }
}
export async function readTodos(c: FollowThroughConfig) {
  if (!c.enabled) throw new FollowThroughError('profile_disabled');
  const authority = await registryAdmission(c);
  const store = await openLocalAuthorityStore(c.runtime_root, c.goal_id, {}, {existingOnly: true});
  const head = await store.loadAuthority();
  if (head.status !== 'loaded') throw new FollowThroughError('canonical_authority_unavailable');
  const collection = canonicalTodoCollection(head.head, c.goal_id, false);
  if (!await authority.current()) throw new FollowThroughError('registry_changed');
  return {registry_digest: authority.digest, revision: head.provider_revision, todos: collection.projection.todo_ids.map(id => collection.projection.todos.get(id)!)};
}
/** Explicitly refresh a selected review against current state without another model call. */
export async function refreshReview(configPath: string, value: unknown, reader?: LarkReader): Promise<ReviewPacket> {
  const c = await loadConfig(configPath);
  if (!c.enabled) throw new FollowThroughError('profile_disabled');
  const packet = object(value, 'review') as unknown as ReviewPacket;
  if (packet.schema_version !== 'personal_follow_through_review_v0' || packet.config_digest !== digest(c)) throw new FollowThroughError('review_identity_changed');
  const authority = await registryAdmission(c);
  if (authority.digest !== packet.registry_digest) throw new FollowThroughError('registry_changed_review_again');
  await verifySource(configPath, c, packet, reader);
  return buildReview(c, packet.source, packet.candidate, await readTodos(c));
}
async function verifySource(configPath: string, c: FollowThroughConfig, packet: ReviewPacket, reader?: LarkReader) {
  const fresh = await captureLark(c, packet.source.start, packet.source.end, () => profileCurrent(configPath, c, packet.registry_digest), reader);
  const {capturedAt: _old, ...oldSource} = packet.source;
  const {capturedAt: _new, ...newSource} = fresh;
  if (digest(oldSource) !== digest(newSource)) throw new FollowThroughError('source_changed_review_again');
}
export async function applyReview(configPath: string, value: unknown, approvedDigest: string, reader?: LarkReader): Promise<JsonObject> {
  const c = await loadConfig(configPath);
  if (!c.enabled) throw new FollowThroughError('profile_disabled');
  const packet = object(value, 'review') as unknown as ReviewPacket;
  if (packet.schema_version !== 'personal_follow_through_review_v0' || digest(packet) !== approvedDigest || packet.config_digest !== digest(c)) throw new FollowThroughError('review_identity_changed');
  const authority = await registryAdmission(c);
  if (authority.digest !== packet.registry_digest) throw new FollowThroughError('registry_changed_review_again');
  await verifySource(configPath, c, packet, reader);
  // Revalidate all model-derived data and bind generated fields to the original review.
  const reconstructed = buildReview(c, packet.source, packet.candidate,
    {registry_digest: packet.registry_digest, revision: packet.expected_provider_revision, todos: packet.candidate.kind === 'amend'
      ? [{schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: packet.todo_id, role: 'user', status: 'open', archive_state: 'active'}] : []}, packet.observed_at);
  for (const field of ['operation_id', 'todo_id', 'config_digest'] as const) if (reconstructed[field] !== packet[field]) throw new FollowThroughError('review_identity_changed');
  if (typeof packet.note !== 'string' || packet.note.length > 8000) throw new FollowThroughError('invalid_review_note');
  const currentGrant = () => profileCurrent(configPath, c, packet.registry_digest);
  return withCanonicalWriter(c.runtime_root, c.goal_id, false, async () => {
    if (!await currentGrant()) throw new FollowThroughError('profile_revoked');
    const store = await openLocalAuthorityStore(c.runtime_root, c.goal_id, {}, {existingOnly: true});
    const common = {goal_id: c.goal_id, actor_agent_id: null, registered_agents: authority.registeredAgents,
      operation_id: packet.operation_id, dry_run: false, now: new Date(packet.observed_at)};
    // Canonical receipt reconciliation precedes revision checks for exact retries.
    let result: JsonObject;
    if (packet.candidate.kind === 'create') {
      const receipt = await store.readReceipt(packet.operation_id);
      if (receipt.status === 'missing') {
        const current = await store.loadAuthority();
        if (current.status !== 'loaded' || current.provider_revision !== packet.expected_provider_revision) throw new FollowThroughError('review_stale');
      }
      result = await executeCoordinationTodoCreate(store, {...common,
        todo: {schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: packet.todo_id, role: 'user',
          status: 'open', done: false, archive_state: 'active', text: packet.candidate.title, note: packet.note}}, currentGrant);
    } else {
      result = await executeCoordinationTodoUpdate(store, {...common, todo_id: packet.todo_id,
        expected_role: 'user', expected_provider_revision: packet.expected_provider_revision,
        patch: {text: packet.candidate.title, note: packet.note}, clear_fields: []}, currentGrant);
    }
    return result;
  });
}
