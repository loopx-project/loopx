import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
const picture = { name: 'reference.png', mimeType: 'image/png', buffer: Buffer.from(PNG, 'base64') };
let daemon, browser, team;
before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
  const template = await (await api(daemon, '/api/teams/template')).json();
  team = await (await api(daemon, '/api/teams', { method: 'POST', body: JSON.stringify({
    ...template, name: 'Image team', members: template.members.map(member => ({ ...member, agent: 'pi' })),
  }) })).json();
});
after(async () => { await browser?.close(); await daemon?.stop(); });

for (const phone of [false, true]) for (const theme of ['light', 'dark']) test(`team pictures: ${phone ? 'phone' : 'desktop'} ${theme} upload, preview, retry and continuation`, async () => {
  const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: phone, hasTouch: phone, colorScheme: theme });
  const page = await context.newPage();
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  try {
    await page.addInitScript(theme => localStorage.setItem('calli.appearance', JSON.stringify({ mode: theme, palette: 'paper' })), theme);
    await openUi(page, daemon);
    await page.locator('.agent-btn').click();
    await page.getByRole('menuitemradio', { name: team.name, exact: true }).click();
    const input = page.locator('.composer input[type=file]');
    assert.equal(await input.getAttribute('accept'), 'image/*');
    await input.setInputFiles(picture);
    const card = page.locator('.composer .attachments .attachment');
    await card.waitFor();
    assert.equal(await card.locator('img').getAttribute('alt'), picture.name);
    await card.locator('.attachment-view').click();
    await page.getByRole('dialog').waitFor();
    await page.keyboard.press('Escape');
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
    await card.locator('.attachment-remove').click();
    assert.equal(await card.count(), 0);
    // Clipboard and drag/drop use the same existing thumbnails as the file picker.
    for (const event of ['paste', 'drop']) {
      await page.locator('.composer textarea').evaluate((element, { event, png }) => {
        const data = new DataTransfer();
        data.items.add(new File([Uint8Array.from(atob(png), c => c.charCodeAt(0))], 'reference.png', { type: 'image/png' }));
        element.dispatchEvent(event === 'paste' ? new ClipboardEvent(event, { clipboardData: data, bubbles: true, cancelable: true }) : new DragEvent(event, { dataTransfer: data, bubbles: true, cancelable: true }));
      }, { event, png: PNG });
      await card.waitFor();
      if (event === 'paste') await card.locator('.attachment-remove').click();
    }
    const route = `**/api/teams/${team.id}/tasks`;
    await page.route(route, request => request.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ error: 'Image upload unavailable' }) }));
    await page.locator('.send-btn').click();
    await page.getByText('Image upload unavailable', { exact: true }).waitFor();
    assert.equal(await card.count(), 1, 'failed sending keeps the image draft');
    await page.unroute(route);
    await page.getByRole('button', { name: '关闭提示', exact: true }).click();
    if (process.env.LOOCHAT_E2E_SHOTS_DIR) {
      mkdirSync(process.env.LOOCHAT_E2E_SHOTS_DIR, { recursive: true });
      await page.screenshot({ path: join(process.env.LOOCHAT_E2E_SHOTS_DIR, `team-images-${phone ? 'phone' : 'desktop'}-${theme}.png`), animations: 'disabled' });
    }
    const dispatched = page.waitForResponse(response => response.url().endsWith(`/api/teams/${team.id}/tasks`) && response.status() === 202);
    await page.locator('.send-btn').click();
    const { threadId } = await (await dispatched).json();
    await page.locator('.sent-image img').waitFor();
    await page.getByRole('button', { name: '确认完成', exact: true }).waitFor();
    await page.getByRole('button', { name: '确认完成', exact: true }).click();
    await page.locator('.team-task-status', { hasText: '已完成' }).waitFor();
    // An image-only message also reopens a completed team task.
    await input.setInputFiles(picture);
    await card.waitFor();
    const continued = page.waitForResponse(response => response.url().endsWith(`/api/threads/${threadId}/turns`) && response.status() === 202);
    await page.locator('.send-btn').click();
    await continued;
    await page.waitForFunction(() => document.querySelectorAll('.sent-image img').length === 2);
    assert.equal(await page.evaluate(() => document.body.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
  } finally { await context.close(); }
});
