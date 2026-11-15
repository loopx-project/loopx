// The look: light or dark and a palette, chosen in the appearance panel, kept on the device.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, launchBrowser } from '../harness.mjs';

let daemon;
let browser;
let page;

before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, colorScheme: 'light' });
  page = await ctx.newPage();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
});

// What the page is set to: the two attributes, the page colour they come to, and what a phone
// paints its status bar with.
const look = () =>
  page.evaluate(() => {
    const root = document.documentElement;
    return {
      theme: root.getAttribute('data-theme'),
      palette: root.getAttribute('data-palette'),
      bg: getComputedStyle(root).getPropertyValue('--bg').trim(),
      meta: document.querySelector('meta[name="theme-color"]').getAttribute('content'),
    };
  });

/** The colour really on screen. It eases over, so this waits for it to arrive. */
const painted = (rgb) => page.waitForFunction((c) => getComputedStyle(document.body).backgroundColor === c, rgb, { timeout: 5_000 });

async function openPanel() {
  await page.locator('.sidebar').getByRole('button', { name: '配置', exact: true }).click();
  await page.locator('.settings-nav').getByRole('button', { name: '外观', exact: true }).click();
  await page.getByRole('region', { name: '外观' }).waitFor();
}

test('out of the box the page follows the system, in the default palette', async () => {
  await openUi(page, daemon);
  assert.deepEqual(await look(), { theme: 'light', palette: 'paper', bg: '#f8f6f1', meta: '#f8f6f1' });
  await painted('rgb(248, 246, 241)');

  await page.emulateMedia({ colorScheme: 'dark' });
  await page.waitForFunction(() => document.documentElement.getAttribute('data-theme') === 'dark');
  assert.deepEqual(await look(), { theme: 'dark', palette: 'paper', bg: '#1b1a18', meta: '#1b1a18' });
  await painted('rgb(27, 26, 24)');
  await page.emulateMedia({ colorScheme: 'light' });
  await page.waitForFunction(() => document.documentElement.getAttribute('data-theme') === 'light');
});

test('a palette and a mode chosen in the panel show at once', async () => {
  await openPanel();
  const panel = page.getByRole('region', { name: '外观' });
  assert.equal(await panel.getByRole('radio', { name: '跟随系统' }).getAttribute('aria-checked'), 'true');
  assert.equal(await panel.getByRole('radio', { name: '纸' }).getAttribute('aria-checked'), 'true');

  await panel.getByRole('radio', { name: 'Nord' }).click();
  assert.equal(await panel.getByRole('radio', { name: 'Nord' }).getAttribute('aria-checked'), 'true');
  assert.equal(await panel.getByRole('radio', { name: '纸' }).getAttribute('aria-checked'), 'false');
  // The page behind the panel is already in the new colours, and so is what a phone paints its status bar with.
  assert.deepEqual(await look(), { theme: 'light', palette: 'nord', bg: '#eceff4', meta: '#eceff4' });
  await painted('rgb(236, 239, 244)');

  await panel.getByRole('radio', { name: '深色' }).click();
  assert.deepEqual(await look(), { theme: 'dark', palette: 'nord', bg: '#2e3440', meta: '#2e3440' });
  await painted('rgb(46, 52, 64)');

  await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
  await panel.waitFor({ state: 'detached' });
});

test('the choice outlives a reload, and a fixed mode does not follow the system', async () => {
  await page.reload();
  await page.locator('.sidebar').getByRole('button', { name: '新线程', exact: true }).waitFor({ timeout: 10_000 });
  // The system is light here; the page stays dark because dark was chosen.
  assert.deepEqual(await look(), { theme: 'dark', palette: 'nord', bg: '#2e3440', meta: '#2e3440' });

  await openPanel();
  const panel = page.getByRole('region', { name: '外观' });
  assert.equal(await panel.getByRole('radio', { name: '深色' }).getAttribute('aria-checked'), 'true');
  assert.equal(await panel.getByRole('radio', { name: 'Nord' }).getAttribute('aria-checked'), 'true');
  await panel.getByRole('radio', { name: '跟随系统' }).click();
  assert.equal((await look()).theme, 'light');
  await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
  await panel.waitFor({ state: 'detached' });
});

test('the look is on the page before the app starts', async () => {
  // A second tab on the same device: the saved choice is applied by index.html, ahead of the bundle.
  const other = await page.context().newPage();
  await other.route('**/assets/*.js', (route) => route.abort());
  await other.goto(daemon.base + '/');
  assert.equal(await other.evaluate(() => document.documentElement.getAttribute('data-palette')), 'nord');
  assert.equal(await other.evaluate(() => document.documentElement.getAttribute('data-theme')), 'light');
  await other.close();
});

test('a palette this build does not know falls back to the default', async () => {
  await page.evaluate(() => localStorage.setItem('calli.appearance', JSON.stringify({ mode: 'dark', palette: 'from-the-future' })));
  await page.reload();
  await page.locator('.sidebar').getByRole('button', { name: '新线程', exact: true }).waitFor({ timeout: 10_000 });
  assert.deepEqual(await look(), { theme: 'dark', palette: 'paper', bg: '#1b1a18', meta: '#1b1a18' });
});

test('the slash menu opens the panel too', async () => {
  await page.locator('.composer textarea').click();
  await page.keyboard.type('/外观');
  await page.locator('.command-item', { hasText: '外观' }).first().waitFor();
  await page.keyboard.press('Enter');
  await page.getByRole('region', { name: '外观' }).waitFor();
  await page.keyboard.press('Escape');
});
