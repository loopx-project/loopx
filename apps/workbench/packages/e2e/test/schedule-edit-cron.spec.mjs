import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon, browser;
before(async () => { daemon = await startDaemon(); browser = await launchBrowser(); });
after(async () => { await browser?.close(); await daemon?.stop(); });

for (const cron of ['0 9,17 * * *', '0 */2 * * *', '*/5 9 * * 1-5', '0 * * * 1']) {
  test(`renaming a schedule preserves custom cron ${cron}`, async (t) => {
    const response = await api(daemon, '/api/schedules', { method: 'POST', body: JSON.stringify({ name: cron, agent: 'pi', cwd: daemon.workdir, prompt: 'Check status', cron, mode: 'new' }) });
    assert.equal(response.status, 201);
    const schedule = await response.json();
    const page = await browser.newPage();
    t.after(() => page.close());
    await openUi(page, daemon);
    await page.goto(`${daemon.base}/#/schedules/${schedule.id}`);
    await page.getByLabel('名称', { exact: true }).fill('Renamed');
    const saved = page.waitForResponse(r => r.url().endsWith(`/api/schedules/${schedule.id}`) && r.request().method() === 'PATCH');
    await page.getByRole('button', { name: '保存修改', exact: true }).click();
    assert.equal((await saved).status(), 200);
    const stored = (await (await api(daemon, '/api/schedules')).json()).find(s => s.id === schedule.id);
    assert.equal(stored.name, 'Renamed');
    assert.equal(stored.cron, cron);
  });
}
