import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createPwaInstall } from '../src/pwa-install';

function fixture({ secure = true, standalone = false, storageBlocked = false } = {}) {
  const values = new Map<string, string>();
  const modes = new Map<string, EventTarget & { matches: boolean }>();
  const browser = Object.assign(new EventTarget(), {
    isSecureContext: secure,
    matchMedia: (query: string) => {
      if (!modes.has(query)) modes.set(query, Object.assign(new EventTarget(), { matches: standalone && query.includes(': standalone)') }));
      return modes.get(query)!;
    },
    localStorage: {
      getItem: (key: string) => { if (storageBlocked) throw new Error('blocked'); return values.get(key) ?? null; },
      setItem: (key: string, value: string) => { if (storageBlocked) throw new Error('blocked'); values.set(key, value); },
    },
  });
  const store = createPwaInstall(browser as unknown as Window);
  let calls = 0;
  function offer(outcome: 'accepted' | 'dismissed' = 'dismissed', fail = false) {
    const event = Object.assign(new Event('beforeinstallprompt', { cancelable: true }), {
      prompt: () => { calls++; return fail ? Promise.reject(new Error('blocked')) : Promise.resolve(); },
      userChoice: Promise.resolve({ outcome }),
    });
    browser.dispatchEvent(event);
    return event;
  }
  return { browser, store, offer, modes, values, calls: () => calls };
}

test('eligibility comes from the browser; clicking consumes the event synchronously and only once', async () => {
  const f = fixture();
  assert.equal(f.store.getSnapshot().available, false);
  assert.equal(f.offer().defaultPrevented, true);
  const installing = f.store.install();
  assert.equal(f.calls(), 1, 'native prompt is invoked within user activation');
  await f.store.install();
  await installing;
  assert.equal(f.calls(), 1);
  assert.equal(f.store.getSnapshot().installed, false);
  assert.ok(f.store.getSnapshot().dismissedUntil > Date.now() + 6.9 * 86400000);
  const reloaded = createPwaInstall(f.browser as unknown as Window);
  assert.equal(reloaded.getSnapshot().dismissedUntil, f.store.getSnapshot().dismissedUntil);
  reloaded.dispose(); f.store.dispose();
});

test('not now suppresses promotion but keeps the explicit settings install usable', async () => {
  const f = fixture(); f.offer('accepted'); f.store.dismiss();
  assert.equal(f.store.getSnapshot().available, true);
  await f.store.install();
  assert.equal(f.store.getSnapshot().accepted, true);
  assert.equal(f.store.getSnapshot().installed, false, 'consent alone is not installation success');
  f.browser.dispatchEvent(new Event('appinstalled'));
  assert.equal(f.store.getSnapshot().installed, true);
  assert.equal(f.store.getSnapshot().accepted, false);
  f.store.dispose();
});

test('an early success receipt is not overwritten by a later prompt result', async () => {
  const f = fixture(); f.offer('accepted');
  const installing = f.store.install();
  f.browser.dispatchEvent(new Event('appinstalled'));
  await installing;
  assert.equal(f.store.getSnapshot().installed, true);
  assert.equal(f.store.getSnapshot().accepted, false);
  assert.equal(f.store.getSnapshot().busy, false);
  f.store.dispose();
});

test('a failed prompt is not reused and a fresh browser offer can recover', async () => {
  const f = fixture(); f.offer('dismissed', true); await f.store.install();
  assert.equal(f.store.getSnapshot().failed, true);
  await f.store.install(); assert.equal(f.calls(), 1);
  f.offer('accepted'); assert.equal(f.store.getSnapshot().failed, false);
  await f.store.install(); assert.equal(f.calls(), 2);
  f.store.dispose();
});

test('standalone and titlebar-overlay windows suppress installation, including a live mode switch', () => {
  const f = fixture({ standalone: true }); f.offer();
  assert.equal(f.store.getSnapshot().installed, true);
  assert.equal(f.store.getSnapshot().available, false);
  const standalone = f.modes.get('(display-mode: standalone)')!;
  standalone.matches = false; standalone.dispatchEvent(new Event('change'));
  f.offer(); assert.equal(f.store.getSnapshot().available, true);
  const overlay = f.modes.get('(display-mode: window-controls-overlay)')!;
  overlay.matches = true; overlay.dispatchEvent(new Event('change'));
  assert.equal(f.store.getSnapshot().installed, true);
  assert.equal(f.store.getSnapshot().available, false);
  f.store.dispose();
});

test('unsafe origins cannot offer installation; blocked storage still allows session suppression', async () => {
  const unsafe = fixture({ secure: false }); unsafe.offer();
  await unsafe.store.install(); assert.equal(unsafe.calls(), 0); unsafe.store.dispose();
  const f = fixture({ storageBlocked: true }); f.offer(); f.store.dismiss();
  assert.ok(f.store.getSnapshot().dismissedUntil > Date.now());
  await f.store.install(); assert.equal(f.calls(), 1); f.store.dispose();
});

test('dismissal synchronizes across tabs, expired values do not suppress, disposal releases listeners', () => {
  const f = fixture(); f.values.set('loopx.install.dismissedUntil', '1');
  f.browser.dispatchEvent(Object.assign(new Event('storage'), { key: 'loopx.install.dismissedUntil' }));
  assert.equal(f.store.getSnapshot().dismissedUntil, 1);
  f.values.set('loopx.install.dismissedUntil', 'NaN');
  f.browser.dispatchEvent(Object.assign(new Event('storage'), { key: null }));
  assert.equal(f.store.getSnapshot().dismissedUntil, 0);
  f.store.dispose(); f.offer(); assert.equal(f.store.getSnapshot().available, false);
});

test('install metadata retains stable app identity and supplies real 192/512 PNGs', () => {
  const root = new URL('../public/', import.meta.url);
  const manifest = JSON.parse(readFileSync(new URL('manifest.webmanifest', root), 'utf8'));
  assert.equal(manifest.id, '/'); assert.equal(manifest.start_url, '/');
  assert.equal(manifest.display, 'standalone');
  for (const size of [192, 512]) {
    const icon = manifest.icons.find((icon: { sizes: string }) => icon.sizes === `${size}x${size}`);
    assert.ok(icon);
    const png = readFileSync(new URL(icon.src.slice(1), root));
    assert.equal(png.subarray(1, 4).toString(), 'PNG');
    assert.equal(png.readUInt32BE(16), size); assert.equal(png.readUInt32BE(20), size);
  }
});
