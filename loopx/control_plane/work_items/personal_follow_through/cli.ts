import {parseArgs} from 'node:util';
import {open} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {captureLark} from '../../../extensions/lark/personal_follow_through.ts';
import {FollowThroughError, buildReview, digest, object, text} from './contract.ts';
import {loadConfig, profileCurrent, readPrivateJson, readTodos, applyReview, refreshReview} from './service.ts';
import {propose} from './model.ts';

async function savePrivate(path: string, data: unknown) {
  // Exclusive creation: never overwrite another review or follow a symlink.
  const handle = await open(path, 'wx', 0o600);
  try {await handle.writeFile(`${JSON.stringify(data, null, 2)}\n`); await handle.sync();}
  finally {await handle.close();}
}
export async function main(args: string[]): Promise<unknown> {
  const {values, positionals} = parseArgs({args, allowPositionals: true, options: {
    config: {type: 'string'}, start: {type: 'string'}, end: {type: 'string'}, output: {type: 'string'},
    packet: {type: 'string'}, index: {type: 'string'}, 'approve-digest': {type: 'string'}, 'allow-model': {type: 'boolean'},
  }});
  if (positionals.length !== 1 || !['prepare', 'inspect', 'refresh', 'apply', 'brief'].includes(positionals[0])) throw new FollowThroughError('usage_prepare_inspect_refresh_apply_brief_with_config');
  const configPath = text(values.config, 'config_path');
  const c = await loadConfig(configPath);
  if (!c.enabled) return {status: 'disabled'};
  if (positionals[0] === 'brief') {
    const head = await readTodos(c);
    return {status: 'loaded', provider_revision: head.revision, source_freshness: 'not_checked',
      todos: head.todos.filter(t => t.role === 'user').map(t => ({todo_id: t.todo_id, text: t.text, status: t.status, note: t.note ?? null}))};
  }
  if (positionals[0] === 'inspect' || positionals[0] === 'apply' || positionals[0] === 'refresh') {
    const input = object(await readPrivateJson(text(values.packet, 'packet_path')), 'packet');
    let packet = input;
    if (input.schema_version === 'personal_follow_through_batch_v0') {
      if (!Array.isArray(input.packets)) throw new FollowThroughError('invalid_batch');
      const selected = values.index ?? (input.packets.length === 1 ? '0' : '');
      if (!/^\d+$/.test(selected) || Number(selected) >= input.packets.length) throw new FollowThroughError('select_packet_index');
      packet = object(input.packets[Number(selected)], 'packet');
    }
    if (positionals[0] === 'inspect') return {status: 'review_ready', digest: digest(packet), packet};
    if (positionals[0] === 'refresh') {
      const refreshed = await refreshReview(configPath, packet);
      await savePrivate(text(values.output, 'output_path'), refreshed);
      return {status: 'review_ready', digest: digest(refreshed), packet: refreshed};
    }
    const approval = text(values['approve-digest'], 'approval_digest', 64);
    if (digest(packet) !== approval) throw new FollowThroughError('review_identity_changed');
    return applyReview(configPath, packet, approval);
  }
  if (!values['allow-model']) throw new FollowThroughError('prepare_requires_explicit_allow_model');
  const output = text(values.output, 'output_path');
  const head = await readTodos(c);
  const source = await captureLark(c, text(values.start, 'start'), text(values.end, 'end'), () => profileCurrent(configPath, c, head.registry_digest));
  if (!await profileCurrent(configPath, c, head.registry_digest)) throw new FollowThroughError('profile_changed');
  const candidates = await propose(c, source, head.todos);
  const packets = candidates.map(v => buildReview(c, source, v, head));
  if (new Set(packets.map(p => p.operation_id)).size !== packets.length) throw new FollowThroughError('ambiguous_multiple_commitments');
  if (!await profileCurrent(configPath, c, head.registry_digest)) throw new FollowThroughError('profile_changed');
  // One packet per review; mutating canonical state invalidates later stale packets.
  // Refresh remaining items after each apply without another model call; review the new digest.
  await savePrivate(output, {schema_version: 'personal_follow_through_batch_v0', packets});
  return {status: 'prepared', count: packets.length, digests: packets.map(digest),
    next: 'Use inspect --packet FILE --index N, then apply --packet FILE --index N --approve-digest DIGEST. Use refresh for remaining items after each mutation and review the new digest; no extra model call.'};
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const result = object(await main(process.argv.slice(2)), 'result');
    process.stdout.write(`${JSON.stringify(result)}\n`);
    if (!['disabled', 'loaded', 'prepared', 'review_ready', 'applied', 'replayed', 'recovered', 'no_change'].includes(String(result.status))) process.exitCode = 1;
  }
  catch (error) {process.stdout.write(`${JSON.stringify({status: 'failed', error: error instanceof FollowThroughError ? error.code : 'unexpected_failure'})}\n`); process.exitCode = 1;}
}
