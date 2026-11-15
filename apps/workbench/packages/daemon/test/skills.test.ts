import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, rmSync, statSync, symlinkSync, readFileSync, utimesSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Server } from 'node:http';
import { AgyAdapter } from '../src/adapters/agy.js';
import { ClaudeAcpAdapter } from '../src/adapters/claude.js';
import { CodexAppServerAdapter } from '../src/adapters/codex.js';
import { PiRpcAdapter } from '../src/adapters/pi.js';
import type { AgentAdapter } from '../src/adapters/types.js';
import { parseFrontmatter, type SkillPayload } from '../src/skills.js';
import { createHttpServer } from '../src/http.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { Workdirs } from '../src/workdirs.js';
import { AttachmentStore } from '../src/attachments.js';

const here = dirname(fileURLToPath(import.meta.url));
const TOKEN = 'test-token-0123456789';

/**
 * The tree under test: each agent's personal folder plus the shared `~/.agents/skills`, with a
 * skill in two places (once changed, so the digests differ) and one that pi, Codex and agy can see.
 *
 *   home/.claude/skills/video      (claude)
 *   home/.pi/agent/skills/video    (pi, older copy: same name, other content)
 *   home/.pi/agent/skills/git      (pi)
 *   home/.agents/skills/shared     (pi + Codex + agy through the shared folder)
 *   home/.gemini/antigravity-cli/skills/empty-no-skillmd  (agy, not a skill: no SKILL.md)
 */
let home: string;
let claudeHome: string;
let piHome: string;
let agyHome: string;
let codexHome: string;
let workdir: string;
let dataDir: string;
let server: Server;
let base: string;
let manager: ThreadManager;

const auth = { Authorization: `Bearer ${TOKEN}` };

function api(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(base + path, { ...init, headers: { 'Content-Type': 'application/json', ...auth, ...(init.headers ?? {}) } });
}

/** One skill folder, the way the agents themselves store them. */
function seed(root: string, name: string, frontmatter: string, extra: Array<{ path?: string; content?: string; exec?: boolean }> = []): string {
  const dir = join(root, name);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, 'SKILL.md'), frontmatter);
  for (const file of extra) {
    const path = join(dir, file.path ?? 'notes.md');
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, file.content ?? 'x');
    if (file.exec) chmodSync(path, 0o755);
  }
  return dir;
}

before(async () => {
  home = mkdtempSync(join(tmpdir(), 'calli-skills-home-'));
  claudeHome = join(home, '.claude');
  piHome = join(home, '.pi', 'agent');
  agyHome = join(home, '.gemini', 'antigravity-cli');
  codexHome = join(home, '.codex');
  for (const dir of [join(claudeHome, 'skills'), join(piHome, 'skills'), join(agyHome, 'skills'), join(home, '.agents', 'skills'), join(codexHome, 'skills')]) {
    mkdirSync(dir, { recursive: true });
  }
  seed(claudeHome, 'skills/video', '---\nname: video\ndescription: Make videos from text\n---\n\n# Video\n', [
    { path: 'scripts/render.sh', content: '#!/bin/sh\necho render\n', exec: true },
  ]);
  // The same skill on pi's side, a little older and a little different: the files themselves
  // carry the date, because that is what the listing reads.
  const piVideo = seed(piHome, 'skills/video', '---\nname: video\ndescription: Make videos (old copy)\n---\n');
  const past = new Date(Date.now() - 86_400_000);
  utimesSync(piVideo, past, past);
  utimesSync(join(piVideo, 'SKILL.md'), past, past);
  seed(piHome, 'skills/git', '---\nname: git\ndescription: Use worktrees\n---\n');
  seed(home, '.agents/skills/shared', '---\nname: shared\ndescription: Folded description\n---\n');
  mkdirSync(join(agyHome, 'skills', 'not-a-skill')); // a folder without SKILL.md is nothing

  workdir = mkdtempSync(join(tmpdir(), 'calli-skills-work-'));
  dataDir = mkdtempSync(join(tmpdir(), 'calli-skills-data-'));
  // The shared `~/.agents/skills` follows HOME, so every adapter carries the test home with it.
  const env = { HOME: home };
  const adapters = new Map<string, AgentAdapter>();
  adapters.set('claude', new ClaudeAcpAdapter([process.execPath, join(here, 'fixtures', 'fake-acp.mjs')], { ...env, CLAUDE_CONFIG_DIR: claudeHome }));
  adapters.set('pi', new PiRpcAdapter([process.execPath, join(here, 'fixtures', 'fake-pi.mjs')], { ...env, PI_CODING_AGENT_DIR: piHome }));
  adapters.set('agy', new AgyAdapter([process.execPath, join(here, 'fixtures', 'fake-agy.mjs')], { ...env, CALLI_AGY_HOME: agyHome }));
  adapters.set('codex', new CodexAppServerAdapter([process.execPath, join(here, 'fixtures', 'fake-codex.mjs')], { ...env, CODEX_HOME: codexHome }));
  const store = new Store(dataDir);
  manager = new ThreadManager(store, adapters, new Workdirs([workdir]), new AttachmentStore(dataDir));
  server = createHttpServer({ manager, token: TOKEN, webDir: null, version: 'test' });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});

after(async () => {
  server.close();
  await manager.shutdown();
  rmSync(home, { recursive: true, force: true });
  rmSync(workdir, { recursive: true, force: true });
  rmSync(dataDir, { recursive: true, force: true });
});

test('parseFrontmatter reads plain, quoted and folded values, and ignores the rest', () => {
  const plain = parseFrontmatter('---\nname: video\ndescription: Make videos\n---\nbody');
  assert.deepEqual(plain, { name: 'video', description: 'Make videos' });
  const folded = parseFrontmatter('---\nname: hyper\ndescription: >\n  One line here\n  and more here.\nother: 12\n---\n');
  assert.equal(folded.description, 'One line here and more here.');
  const quoted = parseFrontmatter("---\nname: q\ndescription: 'keeps: its colons'\n---\n");
  assert.equal(quoted.description, 'keeps: its colons');
  const literal = parseFrontmatter('---\ndescription: |\n  line one\n  line two\n---\n');
  assert.equal(literal.description, 'line one\nline two');
  assert.deepEqual(parseFrontmatter('# no frontmatter at all'), { name: null, description: null });
});

test('the listing shows every location, who reads it, and each skill in each place', async () => {
  const res = await api('/api/skills');
  assert.equal(res.status, 200);
  const body = await res.json();
  const shared = body.locations.find((l: { path: string }) => l.path === join(home, '.agents', 'skills'));
  assert.ok(shared, 'the shared folder is a location');
  assert.deepEqual([...shared.agents].sort(), ['agy', 'codex', 'pi']);
  const claudeLoc = body.locations.find((l: { path: string }) => l.path === join(claudeHome, 'skills'));
  assert.deepEqual(claudeLoc.agents, ['claude']);
  assert.equal(claudeLoc.scope, 'personal');
  assert.equal(shared.scope, 'shared');

  const video = body.skills.find((s: { name: string }) => s.name === 'video');
  assert.ok(video, 'video is listed');
  assert.equal(video.description, 'Make videos from text', 'the newest copy speaks for the skill');
  assert.equal(video.locations.length, 2);
  assert.equal(video.locations[0].path, join(claudeHome, 'skills'), 'newest first; the path is the folder it sits in');
  assert.ok(video.digest !== video.locations[1].digest, 'the two copies are not the same skill');

  const sharedSkill = body.skills.find((s: { name: string }) => s.name === 'shared');
  assert.deepEqual([...sharedSkill.agents].sort(), ['agy', 'codex', 'pi'], 'seen through the shared folder');
  assert.ok(!body.skills.some((s: { name: string }) => s.name === 'not-a-skill'), 'no SKILL.md, no skill');
  const names = body.skills.map((s: { name: string }) => s.name);
  assert.deepEqual(names, [...names].sort(), 'listed by name');
});

test('detail reads the SKILL.md of the newest copy', async () => {
  const res = await api(`/api/skills/detail?name=${encodeURIComponent('video')}`);
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.match(body.skillMd, /# Video/);
  assert.equal(body.locations.length, 2);
});

test('export packs files with their paths and their executable bit, and the digest matches', async () => {
  const res = await api(`/api/skills/export?name=${encodeURIComponent('video')}`);
  assert.equal(res.status, 200);
  const payload = (await res.json()) as SkillPayload;
  assert.equal(payload.name, 'video');
  assert.deepEqual(payload.files.map((f) => f.path), ['scripts/render.sh', 'SKILL.md']);
  assert.equal(payload.files[0].mode, 0o755);
  const listed = (await (await api('/api/skills')).json()).skills.find((s: { name: string }) => s.name === 'video');
  assert.equal(payload.digest, listed.digest, 'a payload says it is the skill the listing described');
});

test('install copies a skill from one agent to another on this machine', async () => {
  const res = await api('/api/skills/install', {
    method: 'POST',
    body: JSON.stringify({ from: { name: 'video', path: join(claudeHome, 'skills') }, targets: ['codex'] }),
  });
  assert.equal(res.status, 200);
  const [outcome] = await res.json();
  assert.equal(outcome.status, 'installed');
  assert.equal(outcome.dir, join(codexHome, 'skills', 'video'));
  assert.ok(existsSync(join(codexHome, 'skills', 'video', 'scripts', 'render.sh')));
  assert.equal(statSync(join(codexHome, 'skills', 'video', 'scripts', 'render.sh')).mode & 0o111, 0o111, 'the exec bit travelled');

  // Again: it is there now, and it is the same skill, so that is said instead of a copy.
  const again = await (await api('/api/skills/install', {
    method: 'POST',
    body: JSON.stringify({ from: { name: 'video', path: join(claudeHome, 'skills') }, targets: ['codex'] }),
  })).json();
  assert.equal(again[0].status, 'same');

  // A different skill under the same name is not overwritten without asking.
  writeFileSync(join(codexHome, 'skills', 'video', 'SKILL.md'), '---\nname: video\ndescription: changed here\n---\n');
  const clash = await (await api('/api/skills/install', {
    method: 'POST',
    body: JSON.stringify({ from: { name: 'video', path: join(claudeHome, 'skills') }, targets: ['codex'] }),
  })).json();
  assert.equal(clash[0].status, 'exists');
  const forced = await (await api('/api/skills/install', {
    method: 'POST',
    body: JSON.stringify({ from: { name: 'video', path: join(claudeHome, 'skills') }, targets: ['codex'], overwrite: true }),
  })).json();
  assert.equal(forced[0].status, 'installed');
  const { readFile } = await import('node:fs/promises');
  assert.match(await readFile(join(codexHome, 'skills', 'video', 'SKILL.md'), 'utf8'), /# Video/);
});

test('install takes a payload from another machine, and only a well-formed one', async () => {
  const payload = await (await api(`/api/skills/export?name=${encodeURIComponent('shared')}`)).json();
  const ok = await (await api('/api/skills/install', {
    method: 'POST',
    body: JSON.stringify({ skill: payload, targets: ['claude', 'codex'] }),
  })).json();
  assert.deepEqual(ok.map((o: { status: string }) => o.status), ['installed', 'installed']);
  assert.ok(existsSync(join(claudeHome, 'skills', 'shared', 'SKILL.md')));
  // After the copy the skill reaches every agent that sees it anywhere — not just the newest copy.
  const relisted = await (await api('/api/skills')).json();
  const shared = relisted.skills.find((s: { name: string }) => s.name === 'shared');
  assert.deepEqual([...shared.agents].sort(), ['agy', 'claude', 'codex', 'pi']);
  assert.equal(shared.locations.length, 3, 'the shared folder plus the two copies just made');

  // Wrong shapes are turned away before anything is written.
  for (const files of [
    [{ path: '../escape', data: 'eHg=' }],
    [{ path: '/etc/passwd', data: 'eHg=' }],
    [{ path: 'a/../../b', data: 'eHg=' }],
    [{ path: 'ok.md', data: 'eHg=' }, { path: 'ok.md', data: 'eHg=' }],
  ]) {
    const res = await api('/api/skills/install', { method: 'POST', body: JSON.stringify({ skill: { name: 'bad', files }, targets: ['claude'] }) });
    assert.equal(res.status, 400, JSON.stringify(files));
  }
  const notName = await api('/api/skills/install', { method: 'POST', body: JSON.stringify({ skill: { name: '../no', files: [{ path: 'SKILL.md', data: 'eHg=' }] }, targets: ['claude'] }) });
  assert.equal(notName.status, 400);
  const nobody = await api('/api/skills/install', { method: 'POST', body: JSON.stringify({ skill: payload, targets: [] }) });
  assert.equal(nobody.status, 400);
  // A file past the cap is refused, not truncated.
  const huge = await api('/api/skills/install', {
    method: 'POST',
    body: JSON.stringify({ skill: { name: 'big', files: [{ path: 'SKILL.md', data: Buffer.alloc(5 * 1024 * 1024).toString('base64') }] }, targets: ['claude'] }),
  });
  assert.equal(huge.status, 413);
});

test('remove takes a skill out of one folder only, and only a folder it manages', async () => {
  const gone = await api(`/api/skills?name=git&path=${encodeURIComponent(join(piHome, 'skills'))}`, { method: 'DELETE' });
  assert.equal(gone.status, 204);
  assert.ok(!existsSync(join(piHome, 'skills', 'git')));
  assert.ok(existsSync(join(piHome, 'skills', 'video')), 'the other skills stay');

  const outside = await api(`/api/skills?name=video&path=${encodeURIComponent(join(home, 'elsewhere'))}`, { method: 'DELETE' });
  assert.equal(outside.status, 404);
  const missing = await api(`/api/skills?name=shared&path=${encodeURIComponent(join(piHome, 'skills'))}`, { method: 'DELETE' });
  assert.equal(missing.status, 404);
});

test('an agent whose folders are not known cannot be installed to', async () => {
  const res = await api('/api/skills/install', {
    method: 'POST',
    body: JSON.stringify({ from: { name: 'video', path: join(claudeHome, 'skills') }, targets: ['traex'] }),
  });
  assert.equal(res.status, 200);
  const [outcome] = await res.json();
  assert.equal(outcome.status, 'no_dir');
});


test('network payloads have stable digests, including padded base64 and equal-sized script edits', async () => {
  const payload = { name: 'digest-check', files: [
    { path: 'SKILL.md', data: Buffer.from('# Guide').toString('base64') },
    { path: 'script.sh', data: Buffer.from('echo a').toString('base64'), mode: 0o755 },
  ] };
  const install = async () => (await (await api('/api/skills/install', { method: 'POST', body: JSON.stringify({ skill: payload, targets: ['claude'] }) })).json())[0];
  assert.equal((await install()).status, 'installed');
  assert.equal((await install()).status, 'same');
  payload.files[1].data = Buffer.from('echo b').toString('base64');
  assert.equal((await install()).status, 'exists');
  assert.equal(readFileSync(join(claudeHome, 'skills/digest-check/script.sh'), 'utf8'), 'echo a');
});

test('invalid payloads cannot replace an existing skill', async () => {
  for (const files of [
    [{ path: 'notes.md', data: 'eHg=' }],
    [{ path: 'SKILL.md', data: '%%%' }],
    [{ path: 'SKILL.md', data: 'eHg=' }, { path: 'a', data: 'eHg=' }, { path: 'a/b', data: 'eHg=' }],
    [{ path: 'SKILL.md', data: 'eHg=' }, { path: 'a/.git/config', data: 'eHg=' }],
  ]) {
    const res = await api('/api/skills/install', { method: 'POST', body: JSON.stringify({ skill: { name: 'digest-check', files }, targets: ['claude'], overwrite: true }) });
    assert.equal(res.status, 400);
    assert.equal(readFileSync(join(claudeHome, 'skills/digest-check/SKILL.md'), 'utf8'), '# Guide');
  }
  assert.equal((await api('/api/skills/install', { method: 'POST', body: 'null' })).status, 400);
});

test('symlinked skill roots work, internal links materialize, and external or cyclic links cannot be exported', async () => {
  const folder = seed(join(claudeHome, 'skills'), 'links', '# Links', [{ path: 'actual.txt', content: 'local' }]);
  symlinkSync('actual.txt', join(folder, 'copy.txt'));
  symlinkSync(folder, join(piHome, 'skills', 'linked'));
  // Installing invalidates the listing cache after the fixture changes.
  await api('/api/skills/install', { method: 'POST', body: JSON.stringify({ from: { name: 'video' }, targets: ['traex'] }) });
  const linked = await api('/api/skills/export?name=linked');
  assert.equal(linked.status, 200);
  assert.equal((await linked.json()).files.find((f: { path: string }) => f.path === 'copy.txt').data, Buffer.from('local').toString('base64'));
  writeFileSync(join(home, 'outside.txt'), 'outside');
  symlinkSync(join(home, 'outside.txt'), join(folder, 'outside.txt'));
  assert.equal((await api('/api/skills/export?name=linked')).status, 400);
  rmSync(join(folder, 'outside.txt'));
  symlinkSync('.', join(folder, 'cycle'));
  assert.equal((await api('/api/skills/export?name=linked')).status, 400);
  rmSync(join(folder, 'cycle'));
  const removed = await api(`/api/skills?name=linked&path=${encodeURIComponent(join(piHome, 'skills'))}`, { method: 'DELETE' });
  assert.equal(removed.status, 204);
  assert.equal(readFileSync(join(folder, 'SKILL.md'), 'utf8'), '# Links');
});
