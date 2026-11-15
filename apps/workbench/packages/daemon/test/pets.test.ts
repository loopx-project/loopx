import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import type { AgentAdapter } from '../src/adapters/types.js';
import { AttachmentStore } from '../src/attachments.js';
import { createHttpServer } from '../src/http.js';
import { PET, PetService, keptFrames } from '../src/pets.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';

// The desk's companion: frames fetched once from a pinned commit, then served without a token.

const TOKEN = 'test-token-0123456789';

function fakeDownload(fail?: (url: string) => boolean) {
  const urls: string[] = [];
  const download = async (url: string) => {
    urls.push(url);
    if (fail?.(url)) throw new Error('offline');
    return new TextEncoder().encode(url.endsWith('.txt') ? 'MIT License' : 'RIFFwebp');
  };
  return { urls, download };
}

test('installs the kept frames from the pinned commit, with the license', async () => {
  const root = mkdtempSync(join(tmpdir(), 'calli-pets-'));
  try {
    const { urls, download } = fakeDownload();
    const pets = new PetService(root, download);
    assert.equal(pets.status().installed, false);
    assert.equal(pets.status().commit, PET.commit);
    await Promise.all([pets.install(), pets.install()]);
    const status = pets.status();
    assert.equal(status.installed, true);
    assert.equal(status.commit, PET.commit);
    assert.equal(status.installing, false);
    assert.equal(status.clips.idle, keptFrames(PET.clips.idle));
    // Joined, not doubled: every file fetched once.
    const frames = Object.values(PET.clips).reduce((n, c) => n + keptFrames(c), 0);
    assert.equal(urls.length, frames + 1);
    assert.ok(urls.every((u) => u.includes(PET.commit)));
    assert.equal(readdirSync(join(root, PET.id, 'idle')).length, keptFrames(PET.clips.idle));
    assert.deepEqual(readdirSync(join(root, PET.id, 'idle')).slice(0, 2), ['idle_001.webp', 'idle_003.webp']);
    assert.equal(readFileSync(join(root, PET.id, 'LICENSE.txt'), 'utf8'), 'MIT License');
    assert.equal(existsSync(join(root, `${PET.id}.partial`)), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('a failed download leaves nothing half-installed and reports the error', async () => {
  const root = mkdtempSync(join(tmpdir(), 'calli-pets-'));
  try {
    const pets = new PetService(root, fakeDownload((url) => url.includes('success_')).download);
    await assert.rejects(pets.install());
    const status = pets.status();
    assert.equal(status.installed, false);
    assert.equal(status.commit, PET.commit);
    assert.match(status.error ?? '', /offline/);
    assert.equal(existsSync(join(root, PET.id)), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('frames are served without a token; everything else about pets needs one', async () => {
  const dataDir = mkdtempSync(join(tmpdir(), 'calli-pets-data-'));
  const root = mkdtempSync(join(tmpdir(), 'calli-pets-'));
  const store = new Store(dataDir);
  const manager = new ThreadManager(store, new Map([['pi', {} as AgentAdapter]]), [dataDir], new AttachmentStore(dataDir));
  const pets = new PetService(root, fakeDownload().download);
  const server = createHttpServer({ manager, token: TOKEN, webDir: null, version: 'test', pets });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const auth = { Authorization: `Bearer ${TOKEN}` };
  try {
    assert.equal((await fetch(`${base}/api/pets`)).status, 401);
    const status = await (await fetch(`${base}/api/pets`, { headers: auth })).json();
    assert.equal(status.installed, false); assert.equal(status.commit, PET.commit);
    assert.equal((await fetch(`${base}/pets/${PET.id}/idle/idle_001.webp`)).status, 404);

    const started = await fetch(`${base}/api/pets/install`, { method: 'POST', headers: auth });
    assert.equal(started.status, 202);
    assert.equal((await started.json()).commit, PET.commit);
    for (let i = 0; i < 200 && !pets.status().installed; i++) await new Promise((r) => setTimeout(r, 10));

    const frame = await fetch(`${base}/pets/${PET.id}/idle/idle_001.webp`);
    assert.equal(frame.status, 200);
    assert.equal(frame.headers.get('content-type'), 'image/webp');
    assert.equal(await frame.text(), 'RIFFwebp');
    // Only frame names of a known clip: no other file, no climbing out.
    assert.equal((await fetch(`${base}/pets/${PET.id}/idle/thinking_001.webp`)).status, 404);
    assert.equal((await fetch(`${base}/pets/${PET.id}/LICENSE.txt`)).status, 404);
    assert.equal((await fetch(`${base}/pets/${PET.id}/..%2F..%2Fidle/idle_001.webp`)).status, 404);
    assert.equal((await fetch(`${base}/pets/other/idle/idle_001.webp`)).status, 404);
  } finally {
    server.close();
    await manager.shutdown();
    store.close();
    rmSync(dataDir, { recursive: true, force: true });
    rmSync(root, { recursive: true, force: true });
  }
});
