import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon, browser;
const shots = process.env.CALLI_E2E_SHOTS_DIR;

before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
  if (shots) mkdirSync(shots, { recursive: true });
  for (const title of ['note source', 'other conversation']) {
    const response = await api(daemon, '/api/threads', {
      method: 'POST', body: JSON.stringify({ agent: 'pi', title, cwd: daemon.workdir }),
    });
    assert.equal(response.status, 201);
    const thread = await response.json();
    await api(daemon, `/api/threads/${thread.id}/turns`, {
      method: 'POST', body: JSON.stringify({ text: title }),
    });
  }
});
after(async () => { await browser?.close(); await daemon?.stop(); });

for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
  test(`notes stay with their source after page navigation (${mobile ? 'phone' : 'desktop'}, ${colorScheme})`, async () => {
    const context = await browser.newContext({
      viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 },
      isMobile: mobile, hasTouch: mobile, colorScheme,
    });
    try {
      const page = await context.newPage();
      await openUi(page, daemon);
      const sidebar = async () => {
        if (mobile && !await page.locator('.app.drawer-open').count()) await page.locator('.menu-btn').click();
      };
      const open = async (title) => {
        await sidebar();
        await page.locator('.sidebar .thread-title', { hasText: title }).click();
        await page.locator('.main .thread .markdown').first().waitFor();
      };
      const newThread = async () => {
        await sidebar();
        await page.locator('.sidebar').getByRole('button', { name: '新线程', exact: true }).click();
        await page.locator('.composer .composer-context').waitFor();
      };
      const plans = async () => {
        await sidebar();
        await page.locator('.sidebar').getByRole('button', { name: '计划', exact: true }).click();
        await page.locator('.todos-page').waitFor();
      };
      const noteCount = () => page.locator('.composer .note-strip').count();
      const box = () => page.locator('.composer textarea');
      const addNote = async () => {
        await page.evaluate(() => {
          const answer = document.querySelector('.main .thread .markdown');
          const range = document.createRange();
          range.selectNodeContents(answer);
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
        });
        await page.locator('.selection-menu button', { hasText: '注释' }).click();
        await page.locator('.selection-comment').fill('only for the source');
        await page.locator('.selection-add').click();
        await page.locator('.composer .note-strip').waitFor();
      };

      await open('note source');
      await addNote();
      await box().fill('source draft');

      await plans();
      await newThread();
      // Wait through draft persistence too: a late replay must not become a saved new-thread draft.
      await page.waitForTimeout(600);
      assert.equal(await noteCount(), 0, 'an empty new thread must not inherit the last note');
      assert.equal(await box().inputValue(), '');

      await open('other conversation');
      assert.equal(await noteCount(), 0);
      assert.equal(await box().inputValue(), '');
      await newThread();
      assert.equal(await noteCount(), 0);
      await box().fill('new draft');
      await open('note source');
      assert.equal(await noteCount(), 1);
      assert.equal(await box().inputValue(), 'source draft');

      // The composer unmounts on the plans page. Its last note event must not replay on mount.
      await plans();
      await newThread();
      assert.equal(await noteCount(), 0, 'a new thread must not replay the source note');
      assert.equal(await box().inputValue(), 'new draft');
      if (shots) await page.screenshot({ path: join(shots, `new-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`), animations: 'disabled' });

      await open('note source');
      assert.equal(await noteCount(), 1, 'the source keeps exactly one copy');
      assert.equal(await box().inputValue(), 'source draft');
      await plans();
      await open('note source');
      assert.equal(await noteCount(), 1, 'returning to the source does not duplicate its note');
      if (shots) await page.screenshot({ path: join(shots, `source-${mobile ? 'phone' : 'desktop'}-${colorScheme}.png`), animations: 'disabled' });

      await page.locator('.composer .quote-remove').click();
      await plans();
      await open('note source');
      assert.equal(await noteCount(), 0, 'a removed note stays removed after remount');
      await page.reload();
      await open('note source');
      assert.equal(await noteCount(), 0);
      assert.equal(await box().inputValue(), 'source draft');
      await newThread();
      assert.equal(await noteCount(), 0);
      assert.equal(await box().inputValue(), 'new draft');

      // A later note must still be deliverable, and sending it must not leave a replay behind.
      await open('note source');
      await addNote();
      await page.locator('.composer .send-btn:not(.stop)').click();
      await page.locator('.main .thread .row.user .notes-chip').last().waitFor();
      await page.locator('.composer .note-strip').waitFor({ state: 'detached' });
      await plans();
      await newThread();
      await page.waitForTimeout(600);
      assert.equal(await noteCount(), 0, 'a sent note cannot reappear in a new thread');
      await open('note source');
      assert.equal(await noteCount(), 0);
      assert.equal(await box().inputValue(), '');
    } finally { await context.close(); }
  });
}
