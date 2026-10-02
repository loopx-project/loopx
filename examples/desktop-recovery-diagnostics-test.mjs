import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import assert from 'node:assert/strict';

const script = readFileSync(new URL('../apps/desktop/loopx-control-plane/static/boot.js', import.meta.url), 'utf8');
function page() {
  const elements = new Map();
  const context = {
    document: {querySelector(id) {
      if (!elements.has(id)) elements.set(id, {dataset: {}, setAttribute() {}, focus() {}, select() {this.selected = true;}});
      return elements.get(id);
    }},
    window: {}, navigator: {clipboard: {writeText: async () => {throw new Error('denied');}}},
    setInterval() {},
    performance: {now: () => 0},
  };
  runInNewContext(script, context);
  return {context, elements};
}
test('export uses bounded fields and preserves the last failure after a check', () => {
  const {context, elements} = page();
  context.packet = {app_version: '1.0.0', state: {phase: 'up_to_date'}, last_failure: {
    phase: 'runtime_required', details: {code: 'runtime_setup_required', installed_identity_available: false, revision_matches: false, content: 'PRIVATE', path: 'PRIVATE'},
  }};
  runInNewContext('renderDiagnostics(packet)', context);
  const value = JSON.parse(elements.get('#diagnostics').value);
  assert.equal(value.error_code, 'runtime_setup_required');
  assert.equal(value.failure_phase, 'runtime_required');
  assert.equal(value.installed_identity_available, false);
  assert.ok(!JSON.stringify(value).includes('PRIVATE'));
  context.packet.last_failure.details.code = 'PRIVATE';
  context.packet.app_version = 'PRIVATE';
  runInNewContext('renderDiagnostics(packet)', context);
  assert.ok(!elements.get('#diagnostics').value.includes('PRIVATE'));
});
test('installer failure is actionable and clipboard denial leaves selectable text', async () => {
  const {context, elements} = page();
  runInNewContext('render({phase:"error",details:{code:"runtime_install_exit_23"}})', context);
  assert.match(elements.get('#update-status').textContent, /23/);
  assert.equal(elements.get('#repair').disabled, false);
  await elements.get('#copy-diagnostics').onclick();
  assert.equal(elements.get('#diagnostics').selected, true);
});
test('a different installed runtime asks before anything is replaced', () => {
  const {context, elements} = page();
  const decision = {
    phase: 'runtime_pairing_required',
    details: {
      code: 'runtime_pairing_required',
      installed_revision: 'a'.repeat(40),
      bundled_revision: 'b'.repeat(40),
      installed_identity_available: true,
      revision_matches: false,
    },
  };
  runInNewContext('render(packet)', Object.assign(context, {packet: decision}));
  assert.equal(elements.get('#pairing').hidden, false);
  assert.equal(elements.get('#pairing-installed').textContent, 'a'.repeat(12));
  assert.equal(elements.get('#pairing-bundled').textContent, 'b'.repeat(12));
  assert.match(elements.get('#pairing-status').textContent, /本地服务需要两者一致/);
  // Neither choice is a background install: both stay available and neither
  // runs before the operator picks one.
  assert.equal(elements.get('#pairing-update').disabled, false);
  assert.equal(elements.get('#pairing-align').disabled, false);
  // The chooser stays up while the chosen action runs, and retires only when
  // services connect.
  runInNewContext('render({phase:"installing_runtime",details:{}})', context);
  assert.equal(elements.get('#pairing').hidden, false);
  runInNewContext('render({phase:"connecting",details:{service:"chat"}})', context);
  assert.equal(elements.get('#pairing').hidden, true);
});
test('the pairing decision reaches diagnostics without private detail', () => {
  const {context, elements} = page();
  context.packet = {
    app_version: '1.0.5',
    state: {phase: 'runtime_pairing_required', details: {code: 'runtime_pairing_required'}},
    last_failure: {
      phase: 'runtime_pairing_required',
      details: {
        code: 'runtime_pairing_required',
        installed_revision: 'a'.repeat(40),
        bundled_revision: 'b'.repeat(40),
        installed_identity_available: true,
        revision_matches: false,
        path: 'PRIVATE',
      },
    },
  };
  runInNewContext('renderDiagnostics(packet)', context);
  const value = JSON.parse(elements.get('#diagnostics').value);
  assert.equal(value.error_code, 'runtime_pairing_required');
  assert.equal(value.failure_phase, 'runtime_pairing_required');
  assert.equal(value.revision_matches, false);
  assert.ok(!JSON.stringify(value).includes('PRIVATE'));
});
