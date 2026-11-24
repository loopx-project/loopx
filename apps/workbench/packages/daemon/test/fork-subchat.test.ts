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
  } finally {
    await h.close();
  }
});
