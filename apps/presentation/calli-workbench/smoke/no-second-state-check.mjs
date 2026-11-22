// Guards "the workbench keeps no state of its own": scans src/ (comments stripped) and fails on
//  1. browser storage outside the display-preference keys below,
//  2. `fetch(` / XMLHttpRequest / EventSource / WebSocket / sendBeacon outside src/loopx/,
//  3. write verbs (POST/PUT/PATCH/DELETE) outside the files whose only transport refuses them,
//  4. LoopX write endpoints (/api/chat/todo, /api/actions, request-control) anywhere.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(fileURLToPath(new URL('.', import.meta.url)), '..');
const SRC = join(ROOT, 'src');

// Display preferences of this device. Nothing here describes work, threads or todos.
const STORAGE_KEYS = new Set([
  'calli.host', 'calli.locale', 'calli.appearance',
  'calli.sidebar.width', 'calli.sidebar.hidden', 'calli.sidebar.allHosts',
  'calli.sidebar.collapsed', 'calli.sidebar.activity', 'calli.desk.order',
]);
// Files whose only job is to read/write those keys (the key itself is checked by the scan below).
const STORAGE_FILES = new Set([
  'hooks.ts', 'hosts.ts', 'i18n/index.ts', 'App.tsx', 'components/DeskPage.tsx', 'components/Sidebar.tsx', 'appearance.ts',
]);
// Write verbs may be spelled where every path ends in a refusal: the base Api (its only transport rejects),
// the demo router (in memory), and the adapter itself.
const WRITE_VERB_FILES = (rel) => rel === 'api.ts' || rel.startsWith('demo/') || rel.startsWith('loopx/');
const FORBIDDEN_ENDPOINTS = [/\/api\/chat\/todo/, /\/api\/actions/, /request-control/];
const NETWORK = /\b(fetch\s*\(|XMLHttpRequest|EventSource|WebSocket|sendBeacon)/;
const STORAGE = /\b(localStorage|sessionStorage|indexedDB|document\.cookie|caches\b)/;
const WRITE_VERB = /['"`](POST|PUT|PATCH|DELETE)['"`]/;

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* walk(p);
    else if (/\.(ts|tsx)$/.test(name)) yield p;
  }
}

/** Removes comments, keeping string literals (and line numbers) intact. */
function stripComments(code) {
  let out = '';
  for (let i = 0; i < code.length; ) {
    const c = code[i], n = code[i + 1];
    if (c === '"' || c === "'" || c === '`') {
      let j = i + 1;
      while (j < code.length && code[j] !== c) j += code[j] === '\\' ? 2 : 1;
      out += code.slice(i, j + 1); i = j + 1;
    } else if (c === '/' && n === '/') {
      while (i < code.length && code[i] !== '\n') i++;
    } else if (c === '/' && n === '*') {
      const end = code.indexOf('*/', i + 2);
      const stop = end < 0 ? code.length : end + 2;
      out += code.slice(i, stop).replace(/[^\n]/g, ''); i = stop;
    } else { out += c; i++; }
  }
  return out;
}

const problems = [];
let files = 0;
for (const file of walk(SRC)) {
  files++;
  const rel = relative(SRC, file).split(sep).join('/');
  const lines = stripComments(readFileSync(file, 'utf8')).split('\n');
  lines.forEach((line, i) => {
    const at = `src/${rel}:${i + 1}`;
    if (!rel.startsWith('loopx/') && NETWORK.test(line)) problems.push(`${at}: network access outside src/loopx/: ${line.trim()}`);
    if (!WRITE_VERB_FILES(rel) && WRITE_VERB.test(line)) problems.push(`${at}: write verb outside the refusing transport: ${line.trim()}`);
    if (STORAGE.test(line)) {
      const keys = [...line.matchAll(/['"`](calli\.[\w.]+)['"`]/g)].map((m) => m[1]);
      if (!STORAGE_FILES.has(rel)) problems.push(`${at}: browser storage in a file that is not a display-preference holder: ${line.trim()}`);
      for (const key of keys) if (!STORAGE_KEYS.has(key)) problems.push(`${at}: storage key not on the whitelist: ${key}`);
    }
    for (const re of FORBIDDEN_ENDPOINTS) if (re.test(line)) problems.push(`${at}: forbidden write endpoint ${re}: ${line.trim()}`);
  });
}

// Every storage key literal anywhere in src/ must be whitelisted, wherever the call that uses it lives.
for (const file of walk(SRC)) {
  const rel = relative(SRC, file).split(sep).join('/');
  const code = stripComments(readFileSync(file, 'utf8'));
  for (const m of code.matchAll(/['"`](calli\.[\w.]+)['"`]/g)) {
    if (!STORAGE_KEYS.has(m[1]) && !/^calli\.(daemon|workbench)/.test(m[1])) problems.push(`src/${rel}: key literal ${m[1]} is not a whitelisted preference`);
  }
}

if (problems.length) {
  console.error(`no-second-state: ${problems.length} problem(s)\n` + problems.map((p) => `  ${p}`).join('\n'));
  process.exit(1);
}
console.log(`no-second-state: ok (${files} files scanned; ${STORAGE_KEYS.size} preference keys whitelisted; fetch only in src/loopx/)`);
