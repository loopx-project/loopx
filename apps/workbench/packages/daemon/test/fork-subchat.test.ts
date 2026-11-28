import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { AddressInfo } from 'node:net';
import { AgentRegistry } from '../src/agents.js';
import { AttachmentStore } from '../src/attachments.js';
import { CoordinationManager, coordinationServer } from '../src/coordination.js';
import { createHttpServer, listenPrivate } from '../src/http.js';
import { Store } from '../src/store.js';
import { ThreadManager } from '../src/threads.js';
import { askedText } from '../src/util.js';

const here = dirname(fileURLToPath(import.meta.url));

async function setup() {
  const dir = mkdtempSync(join(tmpdir(), 'loochat-fork-subchat-'));
  const registry = new AgentRegistry(
    ['pi-a', 'pi-b'].map((id) => ({
      id,
      adapter: 'pi',
      label: id,
      command: [process.execPath, join(here, 'fixtures/fake-pi.mjs')],
    })),
    null,
  );
  const store = new Store(join(dir, 'data'));
  const manager = new ThreadManager(store, registry.adapters, [dir], new AttachmentStore(dir));
  const coordination = new CoordinationManager(store, manager, join(dir, 'coord.sock'));
  const ipc = coordinationServer(coordination);
  const server = createHttpServer({ manager, token: 'fork-subchat-token', webDir: null, version: 'test' });
  await listenPrivate(ipc, join(dir, 'coord.sock'));
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;

  const req = async (method: string, path: string, body?: unknown) => {
    const res = await fetch(base + path, {
      method,
      headers: {
        Authorization: 'Bearer fork-subchat-token',
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    return { status: res.status, ok: res.ok, json: () => res.json() as Promise<any> };
  };

  const idle = async (id: string) => {
    for (let i = 0; i < 300; i++) {
      if (manager.getThread(id).status !== 'running') return;
      await new Promise((r) => setTimeout(r, 20));
    }
    throw new Error(`thread ${id} did not become idle`);
  };

  const close = async () => {
    coordination.dispose();
    await manager.shutdown();
    server.closeAllConnections?.();
    ipc.closeAllConnections?.();
    await new Promise<void>((r) => server.close(() => r()));
    await new Promise<void>((r) => ipc.close(() => r()));
    store.close();
    rmSync(dir, { recursive: true, force: true });
  };

  return { dir, store, manager, coordination, req, idle, close };
}

test('grouped fork keeps same cwd, supports optional firstMessage and forkTag, and enables bidirectional on-demand history', async () => {
  const h = await setup();
  try {
    const root = await h.manager.createThread({ agent: 'pi-a', cwd: h.dir, title: '主线重构' });
    await h.manager.startTurn(root.id, { text: '先定主线架构方案', fromUser: true });
    await h.idle(root.id);

    const outsider = await h.manager.createThread({ agent: 'pi-a', cwd: h.dir, title: '无关会话' });
    await h.manager.startTurn(outsider.id, { text: '机密内容', fromUser: true });
    await h.idle(outsider.id);

    const forkARes = await h.req('POST', `/api/threads/${root.id}/fork`, { grouped: true, forkTag: '分析 one page' });
    assert.equal(forkARes.status, 201);
    const forkA = await forkARes.json();
    assert.equal(forkA.cwd, h.dir);
    assert.equal(forkA.forkGroup, root.id);
    assert.equal(forkA.forkTag, '分析 one page');
    assert.equal(forkA.title, '分析 one page');
    assert.equal(forkA.status, 'idle');

    await h.manager.startTurn(forkA.id, { text: '帮我分析 one page 里的指标口径', fromUser: true });
    await h.idle(forkA.id);

    const forkBRes = await h.req('POST', `/api/threads/${root.id}/fork`, {
      grouped: true,
      forkTag: '写代码',
      firstMessage: '给回执补齐状态字段和单测',
    });
    assert.equal(forkBRes.status, 201);
    const forkB = await forkBRes.json();
    assert.equal(forkB.forkGroup, root.id);
    assert.equal(forkB.forkTag, '写代码');
    await h.idle(forkB.id);
    assert.ok(
      h.store.listEvents(forkB.id, 0, -1).some((e) => e.type === 'turn.started' && String(e.data.text).includes('给回执补齐状态字段和单测')),
      'firstMessage starts a turn in the forked sub-conversation',
    );

    const patchRes = await h.req('PATCH', `/api/threads/${forkA.id}`, { forkTag: '指标分析' });
    assert.equal(patchRes.status, 200);
    assert.equal((await patchRes.json()).forkTag, '指标分析');

    const rootReadsA = (await h.manager.references.command(root.id, { op: 'history', target: forkA.id, userInputs: true })) as any;
    assert.ok(Array.isArray(rootReadsA.inputs) && rootReadsA.inputs.some((i: any) => String(i.text).includes('指标口径')));

    const bReadsA = (await h.manager.references.command(forkB.id, { op: 'history', target: forkA.id, userInputs: true })) as any;
    assert.ok(Array.isArray(bReadsA.inputs) && bReadsA.inputs.some((i: any) => String(i.text).includes('指标口径')));

    const aReadsRoot = (await h.manager.references.command(forkA.id, { op: 'history', target: root.id, userInputs: true })) as any;
    assert.ok(Array.isArray(aReadsRoot.inputs) && aReadsRoot.inputs.some((i: any) => String(i.text).includes('主线架构方案')));

    await assert.rejects(
      h.manager.references.command(root.id, { op: 'history', target: outsider.id, userInputs: true }),
      /outside handover chain/,
    );
    await assert.rejects(
      h.manager.references.command(outsider.id, { op: 'history', target: forkA.id, userInputs: true }),
      /outside handover chain/,
    );

    await h.manager.startTurn(root.id, { text: '主线继续', fromUser: true });
    await h.idle(root.id);
    const rootTurn = h.store.listEvents(root.id, 0, -1).filter((e) => e.type === 'turn.started').at(-1)!;
    const rawPrompt = String(rootTurn.data.text);
    assert.match(rawPrompt, /<loochat-forks>[\s\S]*\[指标分析\][\s\S]*<\/loochat-forks>/);
    assert.match(rawPrompt, /<loochat-forks>[\s\S]*\[写代码\][\s\S]*<\/loochat-forks>/);
    assert.equal(askedText(rawPrompt), '主线继续');
    assert.equal(h.store.getThread(root.id)!.preview, '主线继续');

    const handed = await h.manager.takeOver(forkB.id, 'pi-b', undefined, '继续写代码');
    await h.idle(handed.id);
    assert.equal(handed.forkGroup, root.id);
    assert.equal(handed.forkTag, '写代码');
    assert.ok(h.manager.forkFamilyIds(root.id).has(handed.id));
    assert.ok(h.manager.forkFamilyIds(handed.id).has(forkA.id));

    const lightRes = await h.req('POST', `/api/threads/${root.id}/fork`, {
      grouped: true,
      lightweight: true,
      forkTag: '轻量讨论',
    });
    assert.equal(lightRes.status, 201);
    const light = await lightRes.json();
    assert.equal(light.forkGroup, root.id);
    assert.equal(light.forkTag, '轻量讨论');
    assert.equal(light.agentSessionId, null);
    assert.equal(h.store.listEventsOfType(light.id, 'turn.started').length, 0);

    await h.manager.startTurn(light.id, { text: '只聊轻量新话题', fromUser: true });
    await h.idle(light.id);
    const lightTurn = h.store.listEventsOfType(light.id, 'turn.started').at(-1)!;
    assert.match(String(lightTurn.data.text), /<loochat-forks>[\s\S]*主线重构[\s\S]*<\/loochat-forks>/);
    const lightReadsRoot = (await h.manager.references.command(light.id, { op: 'history', target: root.id, userInputs: true })) as any;
    assert.ok(Array.isArray(lightReadsRoot.inputs) && lightReadsRoot.inputs.some((i: any) => String(i.text).includes('主线架构方案')));

    // Cross-agent lightweight fork: switch to another agent (e.g. when quota runs out) and pull parent history on demand.
    const badFullAcross = await h.req('POST', `/api/threads/${root.id}/fork`, {
      grouped: true,
      lightweight: false,
      agent: 'pi-b',
    });
    assert.equal(badFullAcross.status, 400);

    const crossRes = await h.req('POST', `/api/threads/${root.id}/fork`, {
      grouped: true,
      lightweight: true,
      agent: 'pi-b',
      forkTag: '换号续跑',
      firstMessage: '上一个没额度了，你继续主线重构任务',
    });
    assert.equal(crossRes.status, 201);
    const cross = await crossRes.json();
    assert.equal(cross.agent, 'pi-b');
    assert.equal(cross.forkGroup, root.id);
    assert.equal(cross.forkTag, '换号续跑');
    assert.equal(cross.model, null);
    await h.idle(cross.id);
    const crossTurn = h.store.listEventsOfType(cross.id, 'turn.started').at(-1)!;
    assert.match(String(crossTurn.data.text), /<loochat-forks>[\s\S]*主线重构[\s\S]*<\/loochat-forks>/);
    const crossReadsRoot = (await h.manager.references.command(cross.id, { op: 'history', target: root.id, userInputs: true })) as any;
    assert.ok(Array.isArray(crossReadsRoot.inputs) && crossReadsRoot.inputs.some((i: any) => String(i.text).includes('主线架构方案')));
  } finally {
    await h.close();
  }
});

test('lightweight fork succeeds while the source thread is running (including before its first turn finishes)', async () => {
  const h = await setup();
  try {
    // 1. Start a slow first turn so the root thread is actively running before any turn has completed.
    const root = await h.manager.createThread({ agent: 'pi-a', cwd: h.dir, title: '运行中主线' });
    await h.manager.startTurn(root.id, { text: 'slow 正在执行长任务', fromUser: true });
    for (let i = 0; i < 100 && h.manager.getThread(root.id).status !== 'running'; i++) {
      await new Promise((r) => setTimeout(r, 10));
    }
    assert.equal(h.manager.getThread(root.id).status, 'running');

    // Full fork before agentSessionId exists (400) or while a later turn runs (409) is still refused.
    const fullBeforeSession = await h.req('POST', `/api/threads/${root.id}/fork`, { grouped: true });
    assert.ok(fullBeforeSession.status === 400 || fullBeforeSession.status === 409, `expected 400 or 409, got ${fullBeforeSession.status}`);

    // 2. Quick lightweight fork (no firstMessage) succeeds immediately while root is running.
    const quickRes = await h.req('POST', `/api/threads/${root.id}/fork`, {
      grouped: true,
      lightweight: true,
    });
    assert.equal(quickRes.status, 201);
    const quick = await quickRes.json();
    assert.equal(quick.forkGroup, root.id);
    assert.equal(quick.agentSessionId, null);
    assert.equal(quick.status, 'idle');
    assert.equal(h.manager.getThread(root.id).status, 'running');

    // 3. Lightweight fork with firstMessage runs concurrently in the background while root keeps running.
    const bgRes = await h.req('POST', `/api/threads/${root.id}/fork`, {
      grouped: true,
      lightweight: true,
      forkTag: '并行查问题',
      firstMessage: '帮我查个无关的小问题',
    });
    assert.equal(bgRes.status, 201);
    const bg = await bgRes.json();
    await h.idle(bg.id);
    assert.equal(h.manager.getThread(root.id).status, 'running');

    const bgTurn = h.store.listEventsOfType(bg.id, 'turn.started').at(-1)!;
    assert.match(String(bgTurn.data.text), /<loochat-forks>[\s\S]*运行中主线[\s\S]*运行中[\s\S]*<\/loochat-forks>/);
    const bgReadsRoot = (await h.manager.references.command(bg.id, { op: 'history', target: root.id, userInputs: true })) as any;
    assert.ok(Array.isArray(bgReadsRoot.inputs) && bgReadsRoot.inputs.some((i: any) => String(i.text).includes('正在执行长任务')));

    // 4. fork-options accepts ?lightweight=1 cleanly.
    const optsRes = await h.req('GET', `/api/threads/${root.id}/fork-options?lightweight=1`);
    assert.equal(optsRes.status, 200);
    const opts = await optsRes.json();
    assert.equal(opts.worktree.available, false);

    // 5. After a turn finishes (agentSessionId established) and a second turn runs, full fork returns 409 while lightweight still returns 201.
    await h.manager.cancelTurn(root.id);
    await h.idle(root.id);
    await h.manager.startTurn(root.id, { text: '建立会话', fromUser: true });
    await h.idle(root.id);
    assert.ok(h.manager.getThread(root.id).agentSessionId);
    await h.manager.startTurn(root.id, { text: 'slow 二轮运行中', fromUser: true });
    for (let i = 0; i < 100 && h.manager.getThread(root.id).status !== 'running'; i++) {
      await new Promise((r) => setTimeout(r, 10));
    }
    assert.equal((await h.req('POST', `/api/threads/${root.id}/fork`, { grouped: true })).status, 409);
    assert.equal((await h.req('POST', `/api/threads/${root.id}/fork`, { grouped: true, lightweight: true })).status, 201);

    // Cleanly stop the slow turn on root.
    await h.manager.cancelTurn(root.id);
    await h.idle(root.id);
  } finally {
    await h.close();
  }
});

test('lightweight fork supports cross-agent continuation with 0-token Arm 2 anchor, forkKind, and promote', async () => {
  const h = await setup();
  try {
    const root = await h.manager.createThread({ agent: 'pi-a', cwd: h.dir, title: '主线优化' });
    for (const msg of ['第1轮问题', '第2轮方案A与C', '第3轮确认约束', '第4轮最后进度']) {
      await h.manager.startTurn(root.id, { text: msg, fromUser: true });
      await h.idle(root.id);
    }

    // Cross-agent lightweight fork with continueFromSource starts Turn 1 automatically.
    const contRes = await h.req('POST', `/api/threads/${root.id}/fork`, {
      grouped: true,
      lightweight: true,
      agent: 'pi-b',
      continueFromSource: true,
    });
    assert.equal(contRes.status, 201);
    const cont = await contRes.json();
    assert.equal(cont.agent, 'pi-b');
    assert.equal(cont.forkGroup, root.id);
    assert.equal(cont.title, '主线优化（继续）');
    await h.idle(cont.id);

    const starts = h.store.listEventsOfType(cont.id, 'turn.started');
    assert.equal(starts.length, 1);
    const rawPrompt = String(starts[0].data.text);
    // Prompt carries 0-token Arm 2 paired last-3-turns Q&A anchor + paired `loochat history --turns 6` pointer.
    assert.match(rawPrompt, /<loochat-fork-anchor>/);
    assert.match(rawPrompt, /共 4 轮/);
    assert.doesNotMatch(rawPrompt, /第1轮问题/, 'anchor keeps only the last 3 turns');
    assert.match(rawPrompt, /第2轮方案A与C/);
    assert.match(rawPrompt, /第3轮确认约束/);
    assert.match(rawPrompt, /第4轮最后进度/);
    assert.match(rawPrompt, /hi there 第4轮最后进度/, 'anchor includes paired assistant answers');
    assert.match(rawPrompt, new RegExp(`loochat history ${root.id} --turns 6`));
    // <loochat-forks> also points to paired --turns 6 rather than --user-inputs alone.
    assert.match(rawPrompt, /<loochat-forks>[\s\S]*--turns 6[\s\S]*<\/loochat-forks>/);
    // Visible displayText & preview never leak <loochat-fork-anchor>.
    assert.equal(askedText(rawPrompt), '接着上面的事情继续做');
    assert.equal(String(starts[0].data.displayText ?? ''), '接着上面的事情继续做');
    assert.equal(h.store.getThread(cont.id)!.preview, '接着上面的事情继续做');

    // Turn 2 on the same lightweight fork must NOT re-inject <loochat-fork-anchor>.
    await h.manager.startTurn(cont.id, { text: '第二轮追问', fromUser: true });
    await h.idle(cont.id);
    const turn2Prompt = String(h.store.listEventsOfType(cont.id, 'turn.started').at(-1)!.data.text);
    assert.doesNotMatch(turn2Prompt, /<loochat-fork-anchor>/);

    // Cross-agent lightweight fork can still read parent history on demand.
    const historyRead = (await h.manager.references.command(cont.id, { op: 'history', target: root.id, turns: 2 })) as any;
    assert.ok(Array.isArray(historyRead.turns) && historyRead.turns.length === 2);

    // Derived kinds (`fork`, `handoff`, `ask`) persist across fork/create and can be promoted out to top-level topics.
    assert.equal(cont.forkKind, 'handoff');
    const nativeRes = await h.req('POST', `/api/threads/${root.id}/fork`, { grouped: true, lightweight: false, forkKind: 'fork' });
    assert.equal(nativeRes.status, 201);
    const nativeFork = await nativeRes.json();
    assert.equal(nativeFork.forkGroup, root.id);
    assert.equal(nativeFork.forkKind, 'fork');

    const askRes = await h.req('POST', '/api/threads', {
      agent: 'pi-a',
      cwd: h.dir,
      title: '追问：默认标签',
      forkGroup: cont.id, // nesting under a child resolves flatly to the root topic
      forkKind: 'ask',
    });
    assert.equal(askRes.status, 201);
    const ask = await askRes.json();
    assert.equal(ask.forkGroup, root.id);
    assert.equal(ask.forkKind, 'ask');

    const promoteRes = await h.req('PATCH', `/api/threads/${ask.id}`, { forkGroup: null });
    assert.equal(promoteRes.status, 200);
    const promoted = await promoteRes.json();
    assert.equal(promoted.forkGroup, null);
    assert.equal(promoted.forkKind, null);
  } finally {
    await h.close();
  }
});
