import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { LinkTokens } from '../src/link-tokens.js';

const day = (from: Date, days: number) => new Date(from.getTime() + days * 24 * 60 * 60 * 1000);

test('an issued link is accepted until it expires, then says expired', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'link-tokens-'));
  try {
    const start = new Date('2026-11-16T00:00:00Z');
    let now = start;
    const links = new LinkTokens(dir, () => now);
    await links.load();
    const { token, record } = await links.issue(7);
    assert.match(token, /^wl_[0-9a-f]{48}$/);
    assert.equal(record.revokedAt, null);
    assert.equal(Date.parse(record.expiresAt), day(start, 7).getTime());

    assert.deepEqual(links.check(token), { ok: true, id: record.id, expiresAt: record.expiresAt });
    now = day(start, 8);
    assert.deepEqual(links.check(token), { ok: false, reason: 'expired' });
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test('a revoked link says revoked, not expired, even before its time', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'link-tokens-'));
  try {
    const links = new LinkTokens(dir);
    await links.load();
    const { token, record } = await links.issue();
    assert.equal(await links.revoke(record.id), true);
    assert.equal(await links.revoke(record.id), false);
    assert.deepEqual(links.check(token), { ok: false, reason: 'revoked' });
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test('anything else is not a link token: null, so the hub token decides', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'link-tokens-'));
  try {
    const links = new LinkTokens(dir);
    await links.load();
    await links.issue();
    assert.equal(links.check(undefined), null);
    assert.equal(links.check(''), null);
    assert.equal(links.check('not-a-link-token'), null);
    assert.equal(links.check('wl_deadbeef'), null);
    // Two links live side by side; neither disturbs the other.
    const a = await links.issue();
    const b = await links.issue();
    assert.equal(links.check(a.token)?.ok, true);
    assert.equal(links.check(b.token)?.ok, true);
    await links.revoke(a.record.id);
    assert.deepEqual(links.check(a.token), { ok: false, reason: 'revoked' });
    assert.equal(links.check(b.token)?.ok, true);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test('links survive a restart, and the stored file holds hashes only', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'link-tokens-'));
  try {
    const first = new LinkTokens(dir);
    await first.load();
    const { token, record } = await first.issue(3);
    const again = new LinkTokens(dir);
    await again.load();
    assert.deepEqual(again.check(token), { ok: true, id: record.id, expiresAt: record.expiresAt });
    const listed = again.list();
    assert.equal(listed.length, 1);
    assert.equal(listed[0].id, record.id);
    const stored = await readFile(join(dir, 'link-tokens.json'), 'utf8');
    assert.ok(!stored.includes(token), 'the token string itself must not be persisted');
    assert.ok(stored.includes('hash'), 'the hash is what is persisted');
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
