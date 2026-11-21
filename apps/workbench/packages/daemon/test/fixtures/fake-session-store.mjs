// Opt-in native session files for handover tests. Never write outside the explicit fixture root.
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export function recordSession(kind, id, cwd = process.cwd()) {
  const root = process.env.FAKE_SESSION_ROOT;
  if (!root) return;
  const project = cwd.replace(/[^a-zA-Z0-9]/g, '-');
  const paths = {
    claude: join(root, 'projects', project, `${id}.jsonl`),
    pi: join(root, 'sessions', project, `2026-10-12T00-00-00_${id}.jsonl`),
    codex: join(root, 'sessions', '2026', '10', '12', `rollout-2026-10-12-${id}.jsonl`),
    traex: join(root, 'sessions', '2026', '10', '12', `rollout-2026-10-12-${id}.jsonl`),
    agy: join(root, 'brain', id, '.system_generated', 'logs', 'transcript.jsonl'),
  };
  const path = paths[kind];
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify({ type: 'session', id, cwd }) + '\n');
  return path;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  process.stdout.write(recordSession(process.argv[2], process.argv[3], process.argv[4]) ?? '');
}
