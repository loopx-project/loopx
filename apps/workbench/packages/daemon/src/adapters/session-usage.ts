import { readdir, realpath } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { LiveUsage, validCount, type NativeUsage } from './live-usage.js';
import { configDirectory, findSessionFile } from './session-location.js';
import type { UsageSample } from '../telemetry.js';

/** Claude input excludes cache reads/writes; content blocks repeat the whole request's usage. */
export function claudeSessionUsage(line: string): NativeUsage | null {
  if (!line.includes('"usage"')) return null;
  const record = JSON.parse(line);
  const message = record.message, usage = message?.usage;
  if (record.type !== 'assistant' || !message?.id || !usage || !validCount(usage.input_tokens) || !validCount(usage.output_tokens)) return null;
  const read = usage.cache_read_input_tokens, write = usage.cache_creation_input_tokens;
  if (!validCount(read) || !validCount(write)) return null;
  const input = usage.input_tokens + read + write;
  return { at: Date.parse(record.timestamp), sample: {
    id: `claude:${message.id}`, scope: 'request', source: 'claude.session', model: message.model,
    input, output: usage.output_tokens, total: input + usage.output_tokens, cacheRead: read, cacheWrite: write,
  } };
}

/** agy transcripts report input including cached input, with one stable step index per response. */
export function agySessionUsage(line: string): NativeUsage | null {
  if (!line.includes('PLANNER_RESPONSE')) return null;
  const record = JSON.parse(line);
  if (record.type !== 'PLANNER_RESPONSE' || !Number.isInteger(record.step_index)
    || !validCount(record.input_tokens) || !validCount(record.output_tokens)) return null;
  const input = record.input_tokens;
  let output = record.output_tokens;
  // Only correct a cache-as-output anomaly when an independent native total proves the overlap.
  if (validCount(record.total_tokens) && validCount(record.cache_read_tokens) && record.cache_read_tokens > 0
    && output >= record.cache_read_tokens && input + output - record.cache_read_tokens === record.total_tokens) output -= record.cache_read_tokens;
  if (validCount(record.total_tokens) && input + output !== record.total_tokens) return null;
  return { at: Date.parse(record.created_at), sample: {
    id: `agy:${record.step_index}:${record.created_at}`, scope: 'request', source: 'agy.transcript',
    input, output, total: input + output,
    ...(validCount(record.cache_read_tokens) && record.cache_read_tokens <= input ? { cacheRead: record.cache_read_tokens } : {}),
  } };
}

export function claudeLiveUsage(id: string, cwd: string, env: Record<string, string>, emit: (s: UsageSample) => void): LiveUsage {
  const root = join(configDirectory(env, 'CLAUDE_CONFIG_DIR', ['.claude'], cwd), 'projects');
  let main: string | null = null;
  return new LiveUsage(async () => {
    if (!/^[a-z0-9_-]+$/i.test(id)) return [];
    // Search directory names once for resumed sessions from another worktree, never transcript bodies.
    main ??= await findSessionFile(root, id, 1, (name, session) => name === `${session}.jsonl`)
      ?? join(root, (await realpath(cwd).catch(() => cwd)).replace(/[^a-zA-Z0-9-]/g, '-'), `${id}.jsonl`);
    const subdir = join(dirname(main), id, 'subagents');
    const entries = await readdir(subdir, { withFileTypes: true }).catch(() => []);
    return [main, ...entries.filter(e => e.isFile() && e.name.endsWith('.jsonl')).map(e => join(subdir, e.name))];
  }, claudeSessionUsage, emit);
}

export function agyLiveUsage(id: string, cwd: string, env: Record<string, string>, emit: (s: UsageSample) => void): LiveUsage {
  const root = configDirectory(env, 'CALLI_AGY_HOME', ['.gemini', 'antigravity-cli'], cwd);
  return new LiveUsage(async () => /^[a-z0-9_-]+$/i.test(id) ? [join(root, 'brain', id, '.system_generated', 'logs', 'transcript.jsonl')] : [], agySessionUsage, emit);
}
