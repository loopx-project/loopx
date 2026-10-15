import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { createInterface } from 'node:readline';
import { repoRoot, outputDir } from './fixture.mjs';
import { openWorkspacePage } from './scenario-context.mjs';
import { resolveTestPython } from '../../scripts/test-python.mjs';

// Assets and preference IO use the real packaged handler. Unrelated Goal and
// chat APIs use the existing synthetic workspace; no active runtime is touched.
const serverCode = `
import json,pathlib,sys
from loopx.chat_server import ChatHTTPServer,ChatRequestHandler,default_chat_assets_dir
s=ChatHTTPServer(('127.0.0.1',0),ChatRequestHandler)
s.runtime_root=pathlib.Path(sys.argv[1]);s.assets_dir=default_chat_assets_dir();s.verbose=False
print(json.dumps({'port':s.server_address[1]}),flush=True)
s.serve_forever()
`;
async function start(root) {
  const child = spawn(resolveTestPython({ repoRoot }), ['-u', '-c', serverCode, root], {
    cwd: repoRoot, env: { ...process.env, PYTHONPATH: repoRoot }, stdio: ['ignore', 'pipe', 'pipe'],
  });
  let stderr = '';
  child.stderr.on('data', data => { stderr += data; });
  const lines = createInterface({ input: child.stdout });
  let timer;
  try {
    const [line] = await Promise.race([
      once(lines, 'line'),
      once(child, 'exit').then(() => { throw new Error(`Preference server exited: ${stderr}`); }),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Preference server startup timed out')), 30_000); }),
    ]);
    return { child, url: `http://127.0.0.1:${JSON.parse(line).port}/chat/` };
  } catch (error) { child.kill(); throw error; }
  finally { clearTimeout(timer); lines.close(); }
}
async function stop(service) { if (service.child.exitCode === null) { service.child.kill(); await once(service.child, 'exit'); } }
const rows = page => page.locator('.personal-goal-list:not(.is-stopped) .personal-goal-row');
const order = page => rows(page).evaluateAll(elements => elements.map(e => e.dataset.reorderGoal));
async function expectOrder(page, expected) {
  await page.waitForFunction(expected => JSON.stringify([...document.querySelectorAll('.personal-goal-list:not(.is-stopped) .personal-goal-row')].map(e => e.dataset.reorderGoal)) === JSON.stringify(expected), expected);
}

export async function runDurableGoalOrder(browser) {
  const root = await mkdtemp(join(tmpdir(), 'loopx-sidebar-order-'));
  let service;
  const contexts = [];
  let fail = false;
  let holdRead = null;
  const open = async (url, cached) => {
    const context = await openWorkspacePage(browser, url, { gotoWaitUntil: "domcontentloaded", apiOptions: { fixturePort: new URL(url).port }, beforeGoto: async (_api, page) => {
      if (cached) await page.addInitScript(value => localStorage.setItem('loopx-sidebar-goal-order-v1:%2Fstatus.json', JSON.stringify(value)), cached);
      await page.route('**/api/chat/sidebar-goal-order**', async route => {
        const isRead = route.request().method() === 'GET';
        if (fail) { await route.fulfill({ status: 503, json: { ok: false, error: 'Synthetic unavailable preference store' } }); return; }
        const response = await route.fetch(); // Real HTTP owning store; only outage is injected.
        if (isRead && holdRead) await holdRead;
        await route.fulfill({ response });
      });
    } });
    contexts.push(context);
    await pageReady(context.page);
    return context.page;
  };
  const file = join(root, 'presentation', 'sidebar-goal-order-v1.json');
  const stored = async () => JSON.parse(await readFile(file, 'utf8'))['/status.json'];
  const saved = async expected => {
    for (let attempt = 0; attempt < 100; attempt++) {
      try { if (JSON.stringify(await stored()) === JSON.stringify(expected)) return; } catch { /* Await first atomic save. */ }
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    assert.deepEqual(await stored(), expected);
  };
  try {
    service = await start(root);
    const first = await open(service.url);
    const initial = await order(first);
    const startBox = await rows(first).first().locator('.personal-goal-link').boundingBox();
    const endBox = await rows(first).nth(2).boundingBox();
    await first.mouse.move(startBox.x + 40, startBox.y + 20);
    await first.mouse.down();
    await first.mouse.move(endBox.x + 40, endBox.y + endBox.height - 4, { steps: 8 });
    await first.mouse.up();
    const moved = [initial[1], initial[2], initial[0], ...initial.slice(3)];
    await expectOrder(first, moved);
    await saved(moved);
    await first.reload();
    await expectOrder(first, moved);
    await first.screenshot({ path: resolve(outputDir, 'goal-order-before-restart.png'), animations: 'disabled' });
    const oldUrl = service.url;
    await stop(service);
    service = await start(root);
    assert.notEqual(service.url, oldUrl, 'Restart must use a different browser origin');
    const second = await open(service.url);
    await expectOrder(second, moved);
    await second.screenshot({ path: resolve(outputDir, 'goal-order-after-restart.png'), animations: 'disabled' });
    assert.deepEqual(await stored(), moved);

    // Delay a real older read until after a keyboard move. The user's newer
    // choice must remain visible and eventually replace that stale snapshot.
    let release;
    holdRead = new Promise(resolve => { release = resolve; });
    const third = await open(service.url);
    await third.getByRole('button', { name: '调整 Goal 顺序', exact: true }).click();
    await rows(third).first().getByRole('button', { name: /^下移 / }).click();
    const edited = await order(third);
    release(); holdRead = null;
    await saved(edited);
    await expectOrder(third, edited);
    // Rapid repeated keyboard moves are serialized into the last choice.
    const id = edited[0];
    const down = third.locator(`[data-reorder-goal="${id}"]`).getByRole('button', { name: /^下移 / });
    await down.focus(); await third.keyboard.press('Enter'); await third.keyboard.press('Enter');
    const rapid = await order(third);
    await saved(rapid);

    fail = true;
    await rows(third).first().getByRole('button', { name: /^下移 / }).click();
    await third.getByRole('status').filter({ hasText: '未能保存' }).waitFor();
    const unsaved = await order(third);
    await expectOrder(third, unsaved);
    fail = false;
    await rows(third).first().getByRole('button', { name: /^下移 / }).click();
    await saved(await order(third));
    await third.getByRole('status').filter({ hasText: '未能保存' }).waitFor({ state: 'hidden' });

    // A failed initial read cannot authorize replacing an unknown durable order.
    const beforeReadFailure = await stored();
    fail = true;
    const unread = await open(service.url);
    await unread.getByRole('status').filter({ hasText: '未能保存' }).waitFor();
    await unread.getByRole('button', { name: '调整 Goal 顺序', exact: true }).click();
    await rows(unread).first().getByRole('button', { name: /^下移 / }).click();
    await unread.getByRole('status').filter({ hasText: '未能保存' }).waitFor();
    assert.deepEqual(await stored(), beforeReadFailure);
    fail = false;
    await rows(unread).first().getByRole('button', { name: /^下移 / }).click();
    await saved(await order(unread));

    // Browser cache migrates only when the durable source has no saved value.
    const existing = await stored();
    const staleCache = await open(service.url, initial);
    await expectOrder(staleCache, existing);
    await stop(service);
    const migrationRoot = join(root, 'migration');
    service = await start(migrationRoot);
    const migrated = await open(service.url, moved);
    await expectOrder(migrated, moved);
    const migrationFile = join(migrationRoot, 'presentation', 'sidebar-goal-order-v1.json');
    await migrated.waitForFunction(() => localStorage.getItem('loopx-sidebar-goal-order-v1:%2Fstatus.json') !== null);
    for (let attempt = 0; attempt < 100; attempt++) {
      try { await readFile(migrationFile); break; } catch { await new Promise(resolve => setTimeout(resolve, 25)); }
    }
    assert.deepEqual(JSON.parse(await readFile(migrationFile, 'utf8'))['/status.json'], moved);
    return 'Pointer/keyboard order survives refresh and real service restart on a different port; delayed reads, rapid moves, save failure/retry and legacy cache migration verified against the real private file store.';
  } finally {
    for (const context of contexts) await context.close();
    if (service) await stop(service);
    await rm(root, { recursive: true, force: true });
  }
}
async function pageReady(page) { await rows(page).nth(4).waitFor(); }
