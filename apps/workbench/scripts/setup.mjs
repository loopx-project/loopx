#!/usr/bin/env node
// One command to a working .env: creates it from the template if missing, fills in a random
// access token when empty, and prints the token so it can be pasted straight into the browser.
//
//   npm run setup                                   .env at the repository root, for development
//   node scripts/setup.mjs --env FILE [--set K=V]   another file; --set only fills a new file or an empty key
//   node scripts/setup.mjs ... --quiet              no instructions, only errors (install.sh prints its own)
import { randomBytes } from 'node:crypto';
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const examplePath = join(root, '.env.example');

const args = process.argv.slice(2);
let envPath = join(root, '.env');
let quiet = false;
const preset = [];
for (let i = 0; i < args.length; i++) {
  const arg = args[i];
  if (arg === '--env' && args[i + 1]) envPath = resolve(args[++i]);
  else if (arg === '--set' && /^[A-Z_]+=/.test(args[i + 1] ?? '')) preset.push(args[++i]);
  else if (arg === '--quiet') quiet = true;
  else {
    console.error(`Unknown option: ${arg}`);
    process.exit(2);
  }
}
const say = (...lines) => { if (!quiet) console.log(lines.join('\n')); };

if (!existsSync(examplePath)) {
  console.error('.env.example not found — run this from a full checkout of the repository.');
  process.exit(1);
}
const created = !existsSync(envPath);
if (created) {
  mkdirSync(dirname(envPath), { recursive: true });
  copyFileSync(examplePath, envPath);
  say(`Created ${envPath} from the template.`);
}

let lines = readFileSync(envPath, 'utf8').split('\n');
const valueOf = (key) => {
  const at = lines.findIndex((l) => l.startsWith(`${key}=`));
  return { at, value: at >= 0 ? lines[at].slice(key.length + 1).trim() : '' };
};
// A file someone already set up keeps what they chose; a preset only fills what is still empty.
for (const entry of preset) {
  const key = entry.slice(0, entry.indexOf('='));
  const { at, value } = valueOf(key);
  if (!created && value) continue;
  if (at >= 0) lines[at] = entry;
  else lines.push(entry);
}

// RAH_TOKEN is its name from before the rename to Calli; a token set then is kept as it is.
const at = lines.findIndex((l) => /^(CALLI|RAH)_TOKEN=/.test(l));
const current = at >= 0 ? lines[at].slice(lines[at].indexOf('=') + 1).trim() : '';
if (current) {
  say('The access token is already set. To see it:', `  grep -E '^(CALLI|RAH)_TOKEN=' ${envPath}`);
} else {
  const token = randomBytes(24).toString('hex');
  if (at >= 0) lines[at] = `CALLI_TOKEN=${token}`;
  else lines.push(`CALLI_TOKEN=${token}`);
  say('Generated a random access token:', `  ${token}`, 'Use it to sign in at http://127.0.0.1:8787/ .');
}
writeFileSync(envPath, lines.join('\n'), { mode: 0o600 });

if (envPath === join(root, '.env')) say(`
Next:
  npm run build
  CALLI_ENV_FILE=../../.env npm start -w @calli/daemon   # from the repo root`);
