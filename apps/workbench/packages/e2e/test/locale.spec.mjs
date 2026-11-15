// The language: the browser's own by default, or one picked from the settings menu and kept on the device.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, launchBrowser } from '../harness.mjs';

let daemon;
let browser;

before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
});

/** A page already signed in, in a browser that asks for `locale`. */
async function signedIn(locale) {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, locale });
  await ctx.addInitScript((token) => localStorage.setItem('calli.token', token), daemon.token);
  const page = await ctx.newPage();
  await page.goto(daemon.base + '/');
  await page.locator('.sidebar-inner').waitFor({ state: 'attached', timeout: 15_000 });
  return page;
}

const lang = (page) => page.evaluate(() => document.documentElement.lang);

/** Picks a language from the settings menu, which starts the page over in it. */
async function choose(page, settings, menu, name) {
  await page.locator('.sidebar').getByRole('button', { name: settings, exact: true }).click();
  await page.getByRole('menuitem', { name: menu, exact: true }).hover();
  await Promise.all([page.waitForEvent('load'), page.getByRole('menuitemradio', { name, exact: true }).click()]);
  await page.locator('.sidebar-inner').waitFor({ state: 'attached', timeout: 15_000 });
}

test('with nothing chosen, the page speaks the browser\'s language', async () => {
  const ja = await signedIn('ja-JP');
  assert.equal(await lang(ja), 'ja-JP');
  await ja.getByRole('button', { name: '新しいスレッド' }).first().waitFor();
  await ja.context().close();

  // A language the hub does not speak falls back to English.
  const fr = await signedIn('fr-FR');
  assert.equal(await lang(fr), 'en-US');
  await fr.getByRole('button', { name: 'New thread' }).first().waitFor();
  await fr.context().close();
});

test('a language picked in the menu applies everywhere and outlives a reload', async () => {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  await openUi(page, daemon);
  assert.equal(await lang(page), 'zh-CN');

  await choose(page, '配置', '语言', 'English');
  assert.equal(await lang(page), 'en-US');
  await page.getByRole('button', { name: 'New thread' }).first().waitFor();
  assert.equal(await page.locator('.composer textarea').getAttribute('aria-label'), 'Message');

  await page.reload();
  assert.equal(await lang(page), 'en-US');

  // The menu marks what is chosen, and lists every language under its own name.
  await page.locator('.sidebar').getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Language', exact: true }).hover();
  for (const name of ['Follow system', '简体中文', 'English', '日本語']) await page.getByRole('menuitemradio', { name, exact: true }).waitFor();
  assert.equal(await page.getByRole('menuitemradio', { name: 'English', exact: true }).getAttribute('aria-checked'), 'true');
  assert.equal(await page.getByRole('menuitemradio', { name: 'Follow system', exact: true }).getAttribute('aria-checked'), 'false');
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');

  await choose(page, 'Settings', 'Language', '日本語');
  assert.equal(await lang(page), 'ja-JP');
  await page.getByRole('button', { name: '新しいスレッド' }).first().waitFor();

  // Following the system again forgets the choice: this browser asks for Chinese.
  await choose(page, '設定', '言語', 'システムに合わせる');
  assert.equal(await lang(page), 'zh-CN');
  assert.equal(await page.evaluate(() => localStorage.getItem('calli.locale')), null);
  await ctx.close();
});
