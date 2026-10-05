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
test('legacy pairing state reaches diagnostics without private detail', () => {
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

test('terminal runtime identity failure stops waiting and opens recovery immediately', () => {
  const {context, elements} = page();
  const packet = {state:{phase:'runtime_required',details:{code:'runtime_identity_unavailable',bundled_repair_available:false}}};
  runInNewContext('render(packet.state); renderStartup(packet); escalateFromSnapshot(packet.state)', Object.assign(context,{packet}));
  assert.equal(elements.get('main').dataset.state,'error');
  assert.equal(elements.get('#boot-elapsed').textContent,'等待恢复');
  assert.equal(elements.get('.recovery').open,true);
  assert.equal(elements.get('#repair').disabled,true);
  assert.match(elements.get('#status').textContent,/App 保留当前安装/);
});
test('forgetting a discovery preference reconnects without installing a runtime', async () => {
  const {context, elements} = page();
  const calls=[];
  context.window.__TAURI__={core:{invoke:async(command,args)=>{calls.push({command,args});return {phase:'connecting'};}}};
  const packet={phase:'runtime_required',details:{bundled_repair_available:false}};
  runInNewContext('render(packet)',Object.assign(context,{packet}));
  assert.equal(elements.get('#repair').disabled,true);
  assert.equal(calls.length,0,'rendering never installs or selects a runtime');
  await elements.get('#forget-selection').onclick();
  await Promise.resolve();
  assert.equal(calls.length,1);
  assert.equal(calls[0].command,'desktop_update');
  assert.equal(calls[0].args.action,'forget_runtime_selection');
});

test('an environment pin cannot be cleared by forgetting a preference', async () => {
  const {context, elements} = page();
  context.window.__TAURI__ = {core:{invoke:async () => ({
    state:{phase:'runtime_required',details:{code:'runtime_identity_unavailable'}},
    app_version:'1.2.4', runtime_selection:{explicit:true,remembered:true,bundled_repair_available:false},
  })}};
  await runInNewContext('refresh()', context);
  assert.equal(elements.get('#forget-selection').hidden, true);
  assert.match(elements.get('#update-status').textContent, /移除、修正 LOOPX_BIN 后重新打开 App/);
  assert.equal(elements.get('#repair').disabled, true);
});
