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
test('download and verification failures retain distinct bounded recovery causes', () => {
  for (const [code, meaning] of [
    ['update_download_timeout', /下载超时/],
    ['update_network_failed', /网络请求失败/],
    ['update_download_failed', /服务器未能提供安装包/],
    ['update_signature_invalid', /未通过签名校验/],
  ]) {
    const {context, elements} = page();
    context.packet = {app_version:'1.2.4', state:{phase:'error',details:{code,url:'PRIVATE',signature:'PRIVATE'}}};
    runInNewContext('render(packet.state); renderDiagnostics(packet)', context);
    assert.match(elements.get('#update-status').textContent, meaning);
    assert.match(elements.get('#update-status').textContent, /尚未安装/);
    const value = JSON.parse(elements.get('#diagnostics').value);
    assert.equal(value.error_code, code);
    assert.ok(!JSON.stringify(value).includes('PRIVATE'));
  }
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

test('missing Node is diagnosed from observation, not inferred from every exit 1', () => {
  const {context, elements} = page();
  context.packet = {state: {phase: 'error', details: {code: 'runtime_install_exit_1'}},
    environment: {node_found: false, node_version: null, path: 'PRIVATE'}};
  runInNewContext('renderDiagnostics(packet); render(packet.state)', context);
  assert.match(elements.get('#update-status').textContent, /Node.js/);
  assert.equal(JSON.parse(elements.get('#diagnostics').value).environment.node_found, false);
  assert.ok(!elements.get('#diagnostics').value.includes('PRIVATE'));
  context.packet.environment = {node_found: true, node_version: '24.21.0'};
  runInNewContext('renderDiagnostics(packet); render(packet.state)', context);
  assert.doesNotMatch(elements.get('#update-status').textContent, /未找到 Node/);
  delete context.packet.environment;
  runInNewContext('renderDiagnostics(packet); render(packet.state)', context);
  assert.doesNotMatch(elements.get('#update-status').textContent, /未找到 Node/);
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
  runInNewContext('render({phase:"error",details:{code:"update_network_failed"}})', context);
  assert.equal(elements.get('#repair').disabled, true, 'an action response cannot discard known runtime ownership');
  assert.equal(elements.get('#repair').disabled, true);
});

test('feed results do not replace the native startup state', async () => {
  for (const state of [
    {phase:'checking',details:{background:true}},
    {phase:'error',details:{code:'update_network_failed',background:true}},
  ]) {
    for (const startup_state of [{phase:'connecting'},null]) {
    const {context,elements}=page();
    elements.get('main').dataset.state='loading';
    elements.get('#status').textContent='正在打开 LoopX';
    context.window.__TAURI__={core:{invoke:async()=>({
      app_version:'1.2.4', state, startup_state,
    })}};
    await runInNewContext('refresh()',context);
    assert.equal(elements.get('main').dataset.state,'loading');
    assert.equal(elements.get('#status').textContent,startup_state?'正在打开工作区':'正在打开 LoopX');
    assert.doesNotMatch(elements.get('#boot-detail').textContent,/启动已停止/);
    }
  }
});

test('runtime recovery remains visible while the checked update stays actionable', async () => {
  const {context,elements}=page();
  context.window.__TAURI__={core:{invoke:async()=>({
    app_version:'1.2.4',
    state:{phase:'available',details:{channel:'stable',version:'1.2.5',background:true}},
    startup_state:{phase:'runtime_required',details:{code:'runtime_identity_unavailable'}},
    last_failure:{phase:'error',details:{code:'update_network_failed'}},
  })}};
  await runInNewContext('refresh()',context);
  assert.equal(elements.get('main').dataset.state,'error');
  assert.equal(elements.get('#boot-elapsed').textContent,'等待恢复');
  assert.match(elements.get('#status').textContent,/所选运行时尚不能验证/);
  assert.match(elements.get('#update').textContent,/Install update/);
  assert.equal(elements.get('#update').disabled,false);
  assert.equal(JSON.parse(elements.get('#diagnostics').value).error_code,'runtime_identity_unavailable');
});

test('older native payloads retain their startup recovery behavior', async () => {
  const {context,elements}=page();
  context.window.__TAURI__={core:{invoke:async()=>({
    app_version:'1.2.4',state:{phase:'runtime_required',details:{code:'runtime_identity_unavailable'}},
  })}};
  await runInNewContext('refresh()',context);
  assert.equal(elements.get('main').dataset.state,'error');
  assert.equal(elements.get('#boot-elapsed').textContent,'等待恢复');
});
