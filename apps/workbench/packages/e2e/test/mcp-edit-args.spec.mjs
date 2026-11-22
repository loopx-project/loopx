import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon, browser;
before(async () => { daemon = await startDaemon(); browser = await launchBrowser(); });
after(async () => { await browser?.close(); await daemon?.stop(); });

for (const [index, args] of [
  ['MCP Server/index.js'], ['-c', 'print("hello")', "a'b", 'C:\\MCP Server\\'], [''], [],
].entries()) {
  test(`editing an MCP description preserves stored argument array ${index}`, async (t) => {
    const name = `args-${index}`;
    const registered = await api(daemon, '/api/mcp/entries', { method: 'PUT', body: JSON.stringify({ name, description: 'Before', server: { command: 'node', args } }) });
    assert.equal(registered.status, 200);
    const page = await browser.newPage();
    t.after(() => page.close());
    await openUi(page, daemon);
    await page.goto(`${daemon.base}/#/settings/mcp`);
    await page.getByRole('button', { name: `${name} 的操作`, exact: true }).click();
    await page.getByRole('menuitem', { name: '编辑标注' }).click();
    await page.getByLabel('它是干什么的').fill('After');
    const saved = page.waitForResponse(r => r.url().endsWith('/api/mcp/entries') && r.request().method() === 'PUT');
    await page.getByRole('button', { name: '保存', exact: true }).click();
    assert.equal((await saved).status(), 200);
    const entries = await (await api(daemon, '/api/mcp/entries')).json();
    const entry = entries.find(e => e.name === name);
    assert.equal(entry.description, 'After');
    assert.deepEqual(entry.server.args, args);
  });
}
