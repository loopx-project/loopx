import { appendFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';

function append(path, value) {
  mkdirSync(dirname(path), { recursive: true });
  appendFileSync(path, JSON.stringify(value) + '\n');
}

export function claudeUsage(id, cwd, request) {
  const main = join(process.env.CLAUDE_CONFIG_DIR, 'projects', cwd.replace(/[^a-zA-Z0-9-]/g, '-'), `${id}.jsonl`);
  const row = (key, input, output, read, write) => ({ type: 'assistant', timestamp: new Date().toISOString(), message: {
    id: key, model: 'fixture-model', usage: { input_tokens: input, output_tokens: output, cache_read_input_tokens: read, cache_creation_input_tokens: write },
  } });
  const first = row(`main-${request}`, 100, 20, 40, 10);
  append(main, first); append(main, first);
  append(main, { ...first, message: { ...first.message, usage: { ...first.message.usage, output_tokens: 25 } } });
  const sub = row(`sub-${request}`, 40, 10, 20, 0);
  append(join(dirname(main), id, 'subagents', 'agent-fixture.jsonl'), sub);
  append(main, sub); // Some versions replay the same sidechain in the parent transcript.
}

export function agyUsage(id, step) {
  const file = join(process.env.CALLI_AGY_HOME, 'brain', id, '.system_generated', 'logs', 'transcript.jsonl');
  const row = { type: 'PLANNER_RESPONSE', step_index: step, created_at: new Date().toISOString(), input_tokens: 150, cache_read_tokens: 50, output_tokens: 20 };
  append(file, row); append(file, row);
}
