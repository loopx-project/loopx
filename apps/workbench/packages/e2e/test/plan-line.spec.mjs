import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

/** The desktop dock opens by itself; the phone sheet and a reopened panel need the button. */
async function openOverview(page) {
  if (!(await page.locator('.overview-panel').count())) await page.locator('.topbar-overview').click();
  await page.locator('.overview-panel').waitFor();
}

/** The standing panel's switch closes it; the phone sheet closes from Escape. */
async function closeOverview(page, mobile) {
  if (mobile) await page.keyboard.press('Escape');
  else await page.locator('.topbar-overview').click();
  await page.locator('.overview-panel').waitFor({ state: 'detached' });
}

test('the newest plan lives in the overview panel, opens to the whole list, and older plans go back to the conversation', { timeout: 120000 }, async () => {
  const daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [{ id: 'codex', adapter: 'codex', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')] }] }) });
  const shots = process.env.CALLI_E2E_SHOTS_DIR;
  if (shots) mkdirSync(shots, { recursive: true });
  let browser;
  try {
    browser = await launchBrowser();
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const title = `计划行 ${mobile ? '手机' : '桌面'} ${colorScheme}`;
      const res = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'codex', title, cwd: daemon.workdir, permissionMode: 'full_access' }) });
      assert.equal(res.status, 201);
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(String(error)));
        await openUi(page, daemon);
        if (mobile) await page.locator('.menu-btn').click();
        await page.locator('.thread-title', { hasText: title }).first().click();
        await page.locator('.composer textarea').fill('longplan tool slow');
        await page.getByRole('button', { name: '发送', exact: true }).click();

        // On a desktop the plan is no longer a top-bar button of its own: it is one group of the
        // overview. A phone keeps the ring button it had, beside the overview.
        await page.locator('.topbar-overview').waitFor();
        if (mobile) await page.locator('.topbar-plan').waitFor();
        else assert.equal(await page.locator('.topbar-plan').count(), 0, 'no separate plan button on the desktop');
        // A desktop panel stands open by itself; a phone opens its sheet from the button.
        await openOverview(page);
        const panel = page.locator('.overview-panel');
        await panel.waitFor();
        // A dock already open before the plan arrived keeps its frame. Its fixed footer
        // exposes new sections without inserting a group midway through the person's reading.
        await panel.locator('.overview-foot .overview-trace').click();
        const details = page.locator('.overview-details-page');
        await details.waitFor();
        const plan = details.locator('.overview-plan');
        await plan.waitFor();
        assert.equal(await plan.locator('.plan-entry').count(), 5);
        assert.equal(await plan.locator('.plan-entry.active').count(), 1);
        assert.match(await plan.locator('.plan-entry.active').innerText(), /Pull every root request/);
        await details.locator('.overview-details-head button').click();
        await panel.waitFor();
        if (shots) await page.screenshot({ path: join(shots, `plan-line-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`) });

        // Stopped: nothing is in progress any more.
        await closeOverview(page, mobile);
        await page.getByRole('button', { name: '停止', exact: true }).click();
        await openOverview(page);
        await panel.locator('.overview-plan .plan-entry.active').waitFor({ state: 'detached' });
        await closeOverview(page, mobile);

        // A new message: the old plan goes back into the conversation, where its turn is.
        await page.locator('.composer textarea').fill('hello');
        await page.getByRole('button', { name: '发送', exact: true }).click();
        await page.locator('.plan-toggle').filter({ hasText: '计划 1/5' }).waitFor();
        await openOverview(page);
        assert.equal(await panel.locator('.overview-plan').count(), 0, 'the old plan is not shown twice');
        await closeOverview(page, mobile);
        assert.deepEqual(errors, []);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no horizontal overflow');
      } finally { await context.close(); }
    }
  } finally { await browser?.close(); await daemon.stop(); }
});
