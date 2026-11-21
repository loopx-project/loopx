import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, launchBrowser } from '../harness.mjs';

let daemon;
let browser;
let page;

before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  page = await context.newPage();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
});

test('IME confirmation Enter keeps the draft while an ordinary Enter sends it', async () => {
  await openUi(page, daemon);
  await page.locator('.composer .context-btn').first().click();
  await page.getByRole('menuitemradio', { name: /pi/ }).click();

  const input = page.locator('.composer textarea');
  await input.fill('输入法确认');
  await input.evaluate((element) => {
    // Safari can end composition before keydown, but keeps the legacy IME key code.
    const event = new KeyboardEvent('keydown', {
      key: 'Enter',
      code: 'Enter',
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(event, 'keyCode', { value: 229 });
    element.dispatchEvent(event);
  });

  await page.waitForTimeout(100);
  assert.equal(await input.inputValue(), '输入法确认', 'IME confirmation must not send the draft');

  await input.press('Enter');
  await page.getByText('hi there 输入法确认').waitFor({ timeout: 20_000 });
});
