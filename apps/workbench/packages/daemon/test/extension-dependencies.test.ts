import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { DaemonExtension, Envelope, ExtensionManifest } from '@calli/extension-api';
import { ExtensionHost } from '../src/extension-host.js';
import { createHttpServer } from '../src/http.js';
import { checkManifest } from '../src/extensions.js';
import {
  checkExtensionRequirements,
  resolveRequirements,
  type ProviderCapabilitySnapshot,
} from '../src/extension-dependencies.js';
import { Store } from '../src/store.js';
import { AttachmentStore } from '../src/attachments.js';
import { ThreadManager } from '../src/threads.js';

const text = (s: string) => ({ zh: s, en: s, ja: s });

const manifest = (id: string, extra: Partial<ExtensionManifest> = {}): ExtensionManifest => ({
  id,
  version: '1.0.0',
  apiVersion: 1,
  name: text(id),
  description: text(`${id} extension`),
  permissions: ['events.summary'],
  contributes: { views: [{ id, title: text(id), icon: 'board', route: `#/${id}`, sidebar: { pinned: true } }] },
  ...extra,
});

function mockHost(catalog: ExtensionManifest[], capabilityResolver?: () => readonly ProviderCapabilitySnapshot[] | null | undefined) {
  const dir = mkdtempSync(join(tmpdir(), 'calli-ext-deps-'));
  const store = new Store(dir);
  const manager = new ThreadManager(store, new Map(), [dir], new AttachmentStore(dir));
  const extensions = new ExtensionHost({
    store: store.extensions,
    source: manager,
    catalog,
    capabilityResolver,
  });
  return {
    extensions,
    manager,
    store,
    dir,
    cleanup() {
      extensions.dispose();
      store.close();
      rmSync(dir, { recursive: true, force: true });
    },
  };
}

// ---------------------------------------------------------------- Manifest validation

test('manifest requires: validates valid, empty, invalid, unknown and duplicate declarations', () => {
  // Undefined or empty
  assert.equal(checkExtensionRequirements(undefined), undefined);
  assert.deepEqual(checkExtensionRequirements({}), {});
  assert.deepEqual(checkExtensionRequirements({ agentCapabilities: [] }), { agentCapabilities: [] });

  // Valid known capability
  assert.deepEqual(checkExtensionRequirements({ agentCapabilities: ['realtimeVoice'] }), {
    agentCapabilities: ['realtimeVoice'],
  });

  // checkManifest passes with valid requires
  const valid = manifest('voice-ext', { requires: { agentCapabilities: ['realtimeVoice'] } });
  assert.equal(checkManifest(valid).id, 'voice-ext');

  // Invalid types
  assert.throws(() => checkExtensionRequirements('realtimeVoice'), /requires must be an object/);
  assert.throws(() => checkExtensionRequirements(null), /requires must be an object/);
  assert.throws(() => checkExtensionRequirements([]), /requires must be an object/);
  assert.throws(() => checkExtensionRequirements({ agentCapability: ['realtimeVoice'] }), /unknown requirement field/);
  assert.throws(() => checkExtensionRequirements({ agentCapabilities: 'realtimeVoice' }), /requires\.agentCapabilities must be a list/);
  assert.throws(() => checkExtensionRequirements({ agentCapabilities: [123] }), /requires\.agentCapabilities entries must be strings/);

  // Unknown capability
  assert.throws(
    () => checkExtensionRequirements({ agentCapabilities: ['nonExistent' as any] }),
    /unknown required agent capability: nonExistent/,
  );
  assert.throws(
    () => checkManifest(manifest('bad', { requires: { agentCapabilities: ['superPower' as any] } })),
    /unknown required agent capability: superPower/,
  );

  // Duplicate capability
  assert.throws(
    () => checkExtensionRequirements({ agentCapabilities: ['realtimeVoice', 'realtimeVoice'] }),
    /duplicate required agent capability: realtimeVoice/,
  );
  assert.throws(
    () => checkManifest(manifest('dup', { requires: { agentCapabilities: ['realtimeVoice', 'realtimeVoice'] } })),
    /duplicate required agent capability: realtimeVoice/,
  );
});

// ---------------------------------------------------------------- Old extensions backward compatibility

test('old extensions without requires are 100% compatible and always satisfied', () => {
  const oldExt = manifest('old-board');
  const res = resolveRequirements(oldExt, undefined);
  assert.equal(res.state, 'satisfied');

  const { extensions, cleanup } = mockHost([oldExt]);
  try {
    extensions.start();
    const list = extensions.list();
    assert.equal(list.length, 1);
    assert.equal(list[0]?.requirements?.state, 'satisfied');

    // Installs and enables without any provider snapshot
    const state = extensions.install('old-board', ['events.summary']);
    assert.equal(state.installed, true);
    assert.equal(state.enabled, true);

    const usable = extensions.assertUsable('old-board');
    assert.equal(usable.manifest.id, 'old-board');
  } finally {
    cleanup();
  }
});

// ---------------------------------------------------------------- Default state is unknown (not satisfied)

test('extensions with requires default to unknown and cannot be installed without probed capabilities', () => {
  const voiceExt = manifest('voice', { requires: { agentCapabilities: ['realtimeVoice'] } });
  const { extensions, cleanup } = mockHost([voiceExt]);
  try {
    extensions.start();
    const list = extensions.list();
    assert.equal(list.length, 1);
    assert.equal(list[0]?.requirements?.state, 'unknown');
    assert.equal(list[0]?.enabled, false);

    // Install must be rejected with 400
    assert.throws(() => {
      extensions.install('voice', ['events.summary']);
    }, (err: any) => err.status === 400 && /unknown/.test(err.message));

    // assertUsable throws 404 since it cannot be installed
    assert.throws(() => extensions.assertUsable('voice'), (err: any) => err.status === 404);
  } finally {
    cleanup();
  }
});

// ---------------------------------------------------------------- Missing provider / Missing capability

test('missing provider or missing capability rejects install and setEnabled', () => {
  const voiceExt = manifest('voice', { requires: { agentCapabilities: ['realtimeVoice'] } });
  // Provider snapshot is present but has no providers
  const { extensions, store, cleanup } = mockHost([voiceExt], () => []);
  try {
    extensions.start();
    const list = extensions.list();
    assert.equal(list[0]?.requirements?.state, 'missing');
    assert.deepEqual(list[0]?.requirements?.missing, ['realtimeVoice']);

    // Install fails
    assert.throws(() => {
      extensions.install('voice', ['events.summary']);
    }, (err: any) => err.status === 400 && /missing/.test(err.message));

    // If record exists in DB (e.g. simulated pre-existing install), setEnabled fails
    store.extensions.install('voice', '1.0.0', ['events.summary']);
    assert.throws(() => {
      extensions.setEnabled('voice', true);
    }, (err: any) => err.status === 409 && /missing/.test(err.message));

    // assertUsable throws 403
    assert.throws(() => extensions.assertUsable('voice'), (err: any) => err.status === 403);
  } finally {
    cleanup();
  }
});

// ---------------------------------------------------------------- Provider capability probe error (account readiness is checked separately)

test('provider with error reports error status and rejects install and enable', () => {
  const voiceExt = manifest('voice', { requires: { agentCapabilities: ['realtimeVoice'] } });
  const errorSnapshot: ProviderCapabilitySnapshot[] = [
    {
      providerId: 'probe-failed',
      available: false,
      capabilities: ['realtimeVoice'],
      error: 'Probe failed: SECRET_CREDENTIAL',
    },
  ];

  const { extensions, cleanup } = mockHost([voiceExt], () => errorSnapshot);
  try {
    extensions.start();
    const list = extensions.list();
    assert.equal(list[0]?.requirements?.state, 'error');
    assert.equal(list[0]?.requirements?.reason, 'Provider capability verification failed');
    assert.ok(!JSON.stringify(list).includes('SECRET_CREDENTIAL'));

    assert.throws(() => {
      extensions.install('voice', ['events.summary']);
    }, (err: any) => err.status === 400 && /error/.test(err.message));
  } finally {
    cleanup();
  }
});

// ---------------------------------------------------------------- Satisfied provider, assertUsable, and lifecycle

test('satisfied provider allows installation, and losing provider disables extension while keeping record', async () => {
  const voiceExt = manifest('voice', { requires: { agentCapabilities: ['realtimeVoice'] } });
  let providers: ProviderCapabilitySnapshot[] = [
    {
      providerId: 'codex-host-1',
      available: true,
      capabilities: ['realtimeVoice'],
    },
  ];

  const { extensions, store, cleanup } = mockHost([voiceExt], () => providers);
  try {
    let notifiedDeactivated = false;
    let notifiedActivated = false;

    // Register in-process implementation to verify subscriber sync
    const impl: DaemonExtension = {
      manifest: voiceExt,
      notify(env: Envelope) {
        if (env.hook === 'extension.activated') notifiedActivated = true;
        if (env.hook === 'extension.deactivating') notifiedDeactivated = true;
      },
    };
    extensions.register(impl);
    extensions.start();

    // 1. Initially satisfied
    const initialList = extensions.list();
    assert.equal(initialList[0]?.requirements?.state, 'satisfied');
    assert.equal(initialList[0]?.requirements?.providerId, 'codex-host-1');

    // 2. Install succeeds
    const installed = extensions.install('voice', ['events.summary']);
    assert.equal(installed.installed, true);
    assert.equal(installed.enabled, true);
    assert.equal(installed.requirements?.state, 'satisfied');

    // assertUsable succeeds
    const usable = extensions.assertUsable('voice');
    assert.equal(usable.manifest.id, 'voice');
    assert.equal(usable.providerId, 'codex-host-1');

    // Flush subscriber delivery
    await new Promise((r) => setImmediate(r));
    assert.equal(notifiedActivated, true);

    // 3. Provider becomes unavailable / lost
    let changedFired = false;
    extensions.onChange(() => {
      changedFired = true;
    });

    providers = []; // Lost provider
    extensions.refresh(); // Triggers reconcile

    assert.equal(changedFired, true);

    // SQLite record is preserved with enabled: true!
    const dbRecord = store.extensions.get('voice');
    assert.ok(dbRecord);
    assert.equal(dbRecord.enabled, true);

    // But host.list() reports enabled = false and state = missing
    const afterLostList = extensions.list();
    assert.equal(afterLostList[0]?.installed, true);
    assert.equal(afterLostList[0]?.enabled, false);
    assert.equal(afterLostList[0]?.requirements?.state, 'missing');

    // assertUsable throws 403
    assert.throws(() => extensions.assertUsable('voice'), (err: any) => err.status === 403);

    // Subscriber received deactivation
    assert.equal(notifiedDeactivated, true);

    // 4. Provider recovers
    notifiedActivated = false;
    providers = [
      {
        providerId: 'codex-host-1',
        available: true,
        capabilities: ['realtimeVoice'],
      },
    ];
    extensions.reconcile();

    // Effective state is restored to enabled
    const recoveredList = extensions.list();
    assert.equal(recoveredList[0]?.enabled, true);
    assert.equal(recoveredList[0]?.requirements?.state, 'satisfied');

    // assertUsable succeeds again
    const recoveredUsable = extensions.assertUsable('voice');
    assert.equal(recoveredUsable.providerId, 'codex-host-1');

    await new Promise((r) => setImmediate(r));
    assert.equal(notifiedActivated, true);
  } finally {
    cleanup();
  }
});

// ---------------------------------------------------------------- Manual reconcile injection

test('reconcile(snapshot) injects and clears snapshots explicitly', () => {
  const voiceExt = manifest('voice', { requires: { agentCapabilities: ['realtimeVoice'] } });
  // No resolver passed in options
  const { extensions, cleanup } = mockHost([voiceExt]);
  try {
    extensions.start();
    // Default is unknown
    assert.equal(extensions.list()[0]?.requirements?.state, 'unknown');

    // Inject verified snapshot via reconcile
    extensions.reconcile([
      { providerId: 'probed-provider', available: true, capabilities: ['realtimeVoice'] },
    ]);
    assert.equal(extensions.list()[0]?.requirements?.state, 'satisfied');
    assert.equal(extensions.list()[0]?.requirements?.providerId, 'probed-provider');

    // Install succeeds now
    extensions.install('voice', ['events.summary']);
    assert.equal(extensions.assertUsable('voice').providerId, 'probed-provider');

    // Clear snapshot with null
    extensions.reconcile(null);
    assert.equal(extensions.list()[0]?.requirements?.state, 'unknown');
    assert.equal(extensions.list()[0]?.enabled, false);
    assert.throws(() => extensions.assertUsable('voice'), (err: any) => err.status === 403);
  } finally {
    cleanup();
  }
});

test('a failed resolver blocks dependent extensions without breaking older extensions or leaking probe errors', () => {
  const voice = manifest('voice', { requires: { agentCapabilities: ['realtimeVoice'] } });
  let probes = 0;
  const h = mockHost([manifest('board'), voice], () => { probes++; throw new Error('SECRET_NATIVE_ERROR'); });
  try {
    h.extensions.start();
    h.extensions.install('board', ['events.summary']);
    assert.equal(probes, 0);
    const states = h.extensions.list();
    assert.equal(states[0].enabled, true);
    assert.equal(states[1].requirements?.state, 'error');
    assert.ok(!JSON.stringify(states).includes('SECRET_NATIVE_ERROR'));
    assert.throws(() => h.extensions.install('voice', ['events.summary']), (e: any) => e.status === 400 && !e.message.includes('SECRET_NATIVE_ERROR'));
  } finally { h.cleanup(); }
});

test('refresh discards a manual snapshot; later caller mutations cannot silently change the installed snapshot', () => {
  const h = mockHost([manifest('voice', { requires: { agentCapabilities: ['realtimeVoice'] } })], () => []);
  try {
    const snapshot = [{ providerId: 'verified', available: true, capabilities: ['realtimeVoice'] }];
    h.extensions.reconcile(snapshot);
    h.extensions.install('voice', ['events.summary']);
    snapshot[0].capabilities.length = 0;
    assert.equal(h.extensions.list()[0].enabled, true);
    h.extensions.refresh();
    assert.equal(h.extensions.list()[0].enabled, false);
    assert.equal(h.extensions.list()[0].requirements?.state, 'missing');
    assert.equal(h.store.extensions.get('voice')?.enabled, true);
  } finally { h.cleanup(); }
});

test('HTTP install and enable enforce provider dependencies even without a browser UI', async () => {
  let providers: ProviderCapabilitySnapshot[] = [];
  const h = mockHost([manifest('voice', { requires: { agentCapabilities: ['realtimeVoice'] } })], () => providers);
  const server = createHttpServer({ manager: h.manager, extensions: h.extensions, token: 'test-voice-dependency-token', webDir: null, version: 'test' });
  try {
    h.extensions.start();
    await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
    const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    const request = (path: string, method: string, body: unknown) => fetch(base + path, {
      method, headers: { Authorization: 'Bearer test-voice-dependency-token', 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const blocked = await request('/api/extensions/voice/install', 'POST', { granted: ['events.summary'] });
    assert.equal(blocked.status, 400);
    await blocked.text();
    assert.equal(h.store.extensions.get('voice'), null);
    providers = [{ providerId: 'verified-provider', available: true, capabilities: ['realtimeVoice'] }];
    const installed = await request('/api/extensions/voice/install', 'POST', { granted: ['events.summary'] });
    assert.equal(installed.status, 200);
    assert.equal((await installed.json()).enabled, true);
    const disabled = await request('/api/extensions/voice', 'PATCH', { enabled: false });
    assert.equal(disabled.status, 200); await disabled.text();
    providers = [];
    const enable = await request('/api/extensions/voice', 'PATCH', { enabled: true });
    assert.equal(enable.status, 409); await enable.text();
    assert.equal(h.store.extensions.get('voice')?.enabled, false);
    const unauthorized = await fetch(base + '/api/extensions/voice/install', { method: 'POST' });
    assert.equal(unauthorized.status, 401); await unauthorized.text();
  } finally {
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
    h.cleanup();
  }
});
