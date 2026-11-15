import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { AgentRegistry, defaultInstances } from '../src/agents.js';
import { AttachmentStore } from '../src/attachments.js';
import { createHttpServer } from '../src/http.js';
import { PREVIEW_COOKIE, checkGrant, makeGrant } from '../src/preview.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';

import type { AddressInfo } from 'node:net';

const SECRET = 'test-token-0123456789';

test('a grant opens one thread\u2019s files until it expires', () => {
  const { value } = makeGrant(SECRET, 't1', 1_000_000);
  checkGrant(SECRET, 't1', value, 1_000_000 + 60_000);
  // Another thread, another secret, a mangled value, or past its expiry: refused.
  assert.throws(() => checkGrant(SECRET, 't2', value, 1_000_000 + 60_000));
  assert.throws(() => checkGrant('another-secret', 't1', value, 1_000_000 + 60_000));
  assert.throws(() => checkGrant(SECRET, 't1', `${value}x`, 1_000_000 + 60_000));
  assert.throws(() => checkGrant(SECRET, 't1', value, 1_000_000 + 31 * 60_000));
  assert.throws(() => checkGrant(SECRET, 't1', undefined, 0));
});

test('the route serves a page and its assets to the cookie, and nothing else', async (t) => {
  const workdir = mkdtempSync(join(tmpdir(), 'calli-preview-'));
  const dataDir = mkdtempSync(join(tmpdir(), 'calli-preview-data-'));
  t.after(() => {
    rmSync(workdir, { recursive: true, force: true });
    rmSync(dataDir, { recursive: true, force: true });
  });
  mkdirSync(join(workdir, 'site'));
  writeFileSync(join(workdir, 'site', 'index.html'), '<!doctype html><p>大象自行车</p>');
  writeFileSync(join(workdir, 'site', 'bike.svg'), '<svg xmlns="http://www.w3.org/2000/svg"/>');
  writeFileSync(join(workdir, 'secret.txt'), 'outside the site folder, but still the thread');

  const store = new Store(dataDir);
  const registry = new AgentRegistry(
    defaultInstances({ claudeCommand: ['true'], piCommand: ['true'], agyCommand: ['true'], traexCommand: ['true'], codexCommand: ['true'] }),
    null,
  );
  const manager = new ThreadManager(store, registry.adapters, [workdir], new AttachmentStore(dataDir));
  const server = createHttpServer({ manager, registry, token: SECRET, webDir: null, version: 'test', previewPort: 9999 });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  t.after(() => server.close());
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const auth = { Authorization: `Bearer ${SECRET}`, 'Content-Type': 'application/json' };

  const thread = (await (await fetch(`${base}/api/threads`, { method: 'POST', headers: auth, body: JSON.stringify({ agent: 'pi', cwd: workdir }) })).json()) as { id: string };

  // A link carries the grant as a cookie, and points at the preview port.
  const linkRes = await fetch(`${base}/api/threads/${thread.id}/preview-link?path=${encodeURIComponent(join(workdir, 'site/index.html'))}`, { headers: auth });
  const link = (await linkRes.json()) as { url: string };
  const setCookie = linkRes.headers.get('set-cookie') ?? '';
  const cookie = setCookie.split(';')[0];
  assert.match(cookie, new RegExp(`^${PREVIEW_COOKIE}=`));
  assert.match(setCookie, new RegExp(`Path=/api/threads/${thread.id}/files`));
  assert.match(setCookie, /HttpOnly/);
  assert.match(link.url, new RegExp(`^http://127\.0\.0\.1:9999/api/threads/${thread.id}/files/site/index\.html$`));

  // The file the page starts from, then the asset it references: both open with the cookie alone.
  const pagePath = new URL(link.url).pathname;
  const page = await fetch(`${base}${pagePath}`, { headers: { Cookie: cookie } });
  assert.equal(page.status, 200);
  assert.equal(page.headers.get('content-type'), 'text/html; charset=utf-8');
  assert.equal(page.headers.get('cache-control'), 'no-store');
  assert.match(await page.text(), /大象自行车/);
  const asset = await fetch(`${base}/api/threads/${thread.id}/files/site/bike.svg`, { headers: { Cookie: cookie } });
  assert.equal(asset.status, 200);
  assert.equal(asset.headers.get('content-type'), 'image/svg+xml');

  // Without the cookie, with the wrong thread's cookie, or for a path that walks out: nothing.
  assert.equal((await fetch(`${base}${pagePath}`)).status, 403);
  const otherThread = makeGrant(SECRET, 'not-this-thread').value;
  assert.equal(
    (await fetch(`${base}${pagePath}`, { headers: { Cookie: `${PREVIEW_COOKIE}=${otherThread}` } })).status,
    403,
  );
  const forged = (await fetch(`${base}/api/threads/${thread.id}/preview-link?path=${encodeURIComponent('../../etc/passwd')}`, { headers: auth })).status;
  assert.equal(forged, 404);
  const missing = (await fetch(`${base}/api/threads/${thread.id}/preview-link?path=site/nope.html`, { headers: auth })).status;
  assert.equal(missing, 404);
});
