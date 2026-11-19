// Build in the parent process before Node starts isolated test workers.
import { spawnSync } from 'node:child_process';
import { readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '../..');

function run(command, args, cwd) {
  const result = spawnSync(command, args, { cwd, stdio: 'inherit' });
  if (result.error) console.error(result.error.message);
  return result.status ?? 1;
}

const buildStatus = run('npm', ['run', 'build'], root);
if (buildStatus !== 0) process.exit(buildStatus);

// Explicit arguments are Node test flags and file paths relative to packages/e2e.
// Use --flag=value so option values are not mistaken for file paths.
const args = process.argv.slice(2);
const tests = args.some((arg) => !arg.startsWith('-')) ? [] : readdirSync(join(here, 'test'))
  .filter((name) => name.endsWith('.spec.mjs'))
  .sort()
  .map((name) => join('test', name));
process.exitCode = run(process.execPath, ['--test', '--test-concurrency=2', ...args, ...tests], here);
