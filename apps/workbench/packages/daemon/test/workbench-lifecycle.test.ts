import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, statSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { request } from 'node:http';
import { serveLifecycle } from '../src/workbench-lifecycle.js';

test('private lifecycle fences replacement, refuses busy stop and issues links only on demand', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'wb-life-'));
  const socketPath = join(dir, 'control.sock');
  let links = 0, stops = 0, running = 1;
  const server = await serveLifecycle(socketPath, { url: 'http://127.0.0.1:8796',
    appDir: dir, dataDir: join(dir, 'data'), coreUrl: 'http://127.0.0.1:1',
    running: () => running, loginLink: async () => { links++; return 'private-link'; },
    shutdown: async () => { stops++; },
  });
  const call = (path: string, method = 'GET', instance = '', origin = '') => new Promise<any>((resolve, reject) => {
    const req = request({ socketPath, path, method, headers: { 'x-loopx-instance': instance, ...(origin ? { Origin: origin } : {}) } }, res => {
      let body = ''; res.on('data', c => { body += c; });
      res.on('end', () => resolve({ status: res.statusCode, ...JSON.parse(body) }));
    }); req.on('error', reject); req.end();
  });
  try {
    assert.equal(statSync(socketPath).mode & 0o077, 0);
    const status = await call('/status');
    assert.equal(status.state, 'running');
    assert.equal(status.core.reachable, false);
    assert.equal(status.running_threads, 1);
    assert.equal(links, 0);
    assert.equal((await call('/stop', 'POST', 'stale-instance')).status, 409);
    assert.equal((await call('/stop', 'POST', status.instance_id)).status, 409);
    assert.equal(stops, 0);
    assert.equal((await call('/access', 'POST', status.instance_id, 'https://untrusted.example')).status, 403);
    assert.equal((await call('/access', 'POST', status.instance_id)).access_url, 'private-link');
    assert.equal(links, 1);
    running = 0;
    assert.equal((await call('/stop', 'POST', status.instance_id)).state, 'stopping');
    assert.equal(stops, 1);
    assert.equal((await call('/access', 'POST', status.instance_id)).status, 409);
  } finally {
    await new Promise<void>(resolve => server.close(() => resolve()));
    rmSync(dir, { recursive: true, force: true });
  }
});

test('force stop interrupts a busy daemon only with the observed instance', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'wb-force-'));
  let stopped = false;
  const socketPath = join(dir, 'control.sock');
  const server = await serveLifecycle(socketPath, { url: 'http://localhost:8796', appDir: dir, dataDir: dir,
    coreUrl: 'http://127.0.0.1:1', running: () => 2, loginLink: async () => '', shutdown: async () => { stopped = true; } });
  const call = (path: string, method: string, instance = '') => new Promise<any>((resolve, reject) => {
    const req = request({ socketPath, path, method, headers: { 'x-loopx-instance': instance } }, res => {
      let body = ''; res.on('data', c => { body += c; }); res.on('end', () => resolve(JSON.parse(body)));
    }); req.on('error', reject); req.end();
  });
  try {
    const status = await call('/status', 'GET');
    assert.equal((await call('/stop?force=1', 'POST', status.instance_id)).ok, true);
    assert.equal(stopped, true);
  } finally { await new Promise<void>(resolve => server.close(() => resolve())); rmSync(dir, { recursive: true, force: true }); }
});
