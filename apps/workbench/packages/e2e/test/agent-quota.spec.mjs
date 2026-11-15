import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, launchBrowser, repoRoot } from '../harness.mjs';

let daemon, browser;
before(async () => {
  const command = [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')];
  daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [
    { id: 'codex', adapter: 'codex', label: 'Codex', account: 'primary@example.com', command },
    { id: 'codex2', adapter: 'codex', label: 'Codex 2', account: 'secondary@example.com', command, env: { FAKE_CODEX_WEEKLY_ONLY: '1' } },
    { id: 'pi', adapter: 'pi', command: ['fake-pi'] },
  ] }) });
  browser = await launchBrowser();
});
after(async () => { await browser?.close(); await daemon?.stop(); });

async function openAccounts(page) {
  await page.locator('.agent-btn:visible').click();
  if (!await page.locator('.agent-picker-panel .pop-back').count()) {
    await page.locator('.agent-picker-panel').getByRole('button', { name: /Codex/ }).click();
  }
}
const account = (page, name) => page.locator('.agent-picker-row').filter({ has: page.locator('.pop-item-label', { hasText: new RegExp(`^${name}$`) }) });
const summary = row => row.locator('.agent-quota-summary');
const remaining = row => row.getByRole('meter').evaluateAll(nodes => nodes.map(n => n.getAttribute('aria-valuenow')));
async function shot(page, name) {
  if (!process.env.CALLI_E2E_SHOTS_DIR) return;
  mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
  await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `${name}.png`) });
}
async function fits(page) {
  const panel = page.locator('.agent-picker-panel');
  const bounds = await panel.boundingBox();
  assert.ok(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= page.viewportSize().width + 1 && bounds.y + bounds.height <= page.viewportSize().height + 1);
  assert.equal(await panel.evaluate(el => el.scrollWidth > el.clientWidth), false, JSON.stringify(await panel.evaluate(el => ({width:el.clientWidth,scroll:el.scrollWidth,wide:[...el.querySelectorAll('*')].filter(node=>node.getBoundingClientRect().right>el.getBoundingClientRect().right+1).map(node=>({tag:node.tagName,cls:node.className,width:node.getBoundingClientRect().width,text:node.textContent?.slice(0,100)}))}))));
  for (const button of await page.locator('.agent-quota-summary').all()) {
    assert.equal(await button.evaluate(el => el.scrollWidth > el.clientWidth + 1), false, 'summary fits without clipping a chip');
  }
}

test('single-line rings disclose details without selecting the account in both themes and sizes', async () => {
  for (const width of [1400, 390, 320]) for (const colorScheme of ['light', 'dark']) {
    const mobile = width < 640;
    const context = await browser.newContext({ viewport: { width, height: mobile ? 844 : 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage();
      page.setDefaultTimeout(10_000);
      const quotaRequests = [];
      page.on('request', req => { if (req.url().includes('/quota')) quotaRequests.push(req.url()); });
      await openUi(page, daemon);
      await page.locator('.agent-btn:visible').waitFor();
      assert.ok(quotaRequests.every(url => !url.includes('/codex2/')), 'closed picker only reads the current chat account');
      await openAccounts(page);
      const first = account(page, 'Codex'), second = account(page, 'Codex 2');
      await summary(first).waitFor();
      await summary(second).waitFor();
      await page.mouse.move(1, 1);
      await page.waitForTimeout(350);
      assert.equal(await first.getByRole('meter').count(), 0, 'full details start collapsed');
      assert.ok(quotaRequests.every(url => !url.includes('/pi/')));
      assert.equal(await page.locator('button button').count(), 0, 'separate detail and selection buttons');
      await fits(page);
      await shot(page, `agent-quota-${width}-${colorScheme}`);
      const selectedBefore = await page.locator('.agent-btn:visible').textContent();
      await summary(first).click();
      await first.getByRole('meter').first().waitFor();
      assert.deepEqual(await remaining(first), ['75', '18', '90']);
      assert.equal(await summary(first).getAttribute('aria-expanded'), 'true');
      assert.equal(await page.locator('.agent-btn:visible').textContent(), selectedBefore);
      await first.locator('.agent-quota-details').getByText('代码审查', { exact: true }).waitFor();
      await shot(page, `agent-quota-details-${width}-${colorScheme}`);
      await page.keyboard.press('Escape');
      assert.equal(await summary(first).getAttribute('aria-expanded'), 'false');
      assert.equal(await page.locator('.agent-picker-panel').count(), 1, 'Escape dismisses details first');
      await summary(second).click();
      await second.getByRole('meter').waitFor();
      assert.deepEqual(await remaining(second), ['84'], 'weekly-only account has no invented short window');
      await summary(second).click();
      if (!mobile) {
        await summary(first).hover();
        await page.getByRole('meter').first().waitFor();
        await page.getByRole('tooltip').hover();
        await page.waitForTimeout(200);
        assert.equal(await page.getByRole('tooltip').isVisible(), true, 'preview remains available while reading it');
        await shot(page, `agent-quota-hover-${colorScheme}`);
        await page.mouse.move(1, 1);
        await page.getByRole('meter').first().waitFor({ state: 'hidden' });
        // Keyboard focus provides the same information without needing a mouse.
        await first.getByRole('menuitemradio').focus();
        await page.keyboard.press('Tab');
        assert.equal(await summary(first).evaluate(el => el === document.activeElement), true);
        await page.getByRole('meter').first().waitFor();
        await page.keyboard.press('Enter');
        assert.equal(await summary(first).getAttribute('aria-expanded'), 'true');
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('.agent-picker-panel').count(), 1);
      }
      await second.getByRole('menuitemradio').click();
      assert.equal(await page.locator('.agent-picker-panel').count(), 0);
      assert.match(await page.locator('.agent-btn:visible').textContent(), /Codex 2/);
    } finally { await context.close(); }
  }
});

test('cached quota survives reload and failed reads; unknown, empty and exhausted are distinct', async () => {
  const context = await browser.newContext({ viewport: { width: 320, height: 700 }, isMobile: true, hasTouch: true });
  try {
    const page = await context.newPage();
    page.setDefaultTimeout(10_000);
    await openUi(page, daemon);
    await openAccounts(page);
    await summary(account(page, 'Codex')).waitFor();
    await summary(account(page, 'Codex 2')).waitFor();
    await page.reload();
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    await page.route('**/api/agents/*/quota*', async route => { await pending; await route.fulfill({ status: 503, json: { error: 'unavailable' } }); });
    await openAccounts(page);
    const first = account(page, 'Codex');
    await summary(first).click();
    await first.getByText(/更新中/).waitFor();
    release();
    await first.getByText(/刷新失败，显示上次结果/).waitFor();
    assert.deepEqual(await remaining(first), ['75', '18', '90']);
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
    await page.unroute('**/api/agents/*/quota*');
    await page.route('**/api/agents/*/quota*', route => route.fulfill({ json: {
      windows: route.request().url().includes('/codex2/') ? [] : [{ label: '7H', usedPercent: 100 }, { label: '7D', usedPercent: 0 }],
      checkedAt: new Date().toISOString(),
    } }));
    await openAccounts(page);
    await first.locator('.agent-quota-summary').click();
    await first.getByRole('button', { name: '刷新 codex 的额度', exact: true }).click();
    await page.keyboard.press('Escape');
    await summary(first).getByText('0%', { exact: true }).waitFor().catch(async error => { throw new Error(`${error.message}\n${await page.locator('body').innerText()}`); });
    await summary(first).click();
    await first.getByText('剩余 0%', { exact: true }).waitFor();
    assert.deepEqual(await remaining(first), ['0', '100']);
    await first.getByText('7 小时', { exact: true }).waitFor();
    const empty = account(page, 'Codex 2');
    await empty.getByText('未提供', { exact: true }).waitFor();
    assert.equal(await empty.getByRole('meter').count(), 0);
    assert.equal(await empty.locator('.quota-quick-actions').count(), 0, 'missing quota does not reserve an action row');
    assert.equal(await empty.getByText('服务未提供额度信息', { exact: true }).count(), 0, 'long explanation starts collapsed');
    assert.ok((await empty.locator('.agent-quota').boundingBox()).height <= 32, 'missing quota uses one compact line');
    const selectedBefore = await page.locator('.agent-btn:visible').textContent();
    await empty.locator('.agent-quota-note').click();
    await empty.getByText('服务未提供额度信息', { exact: true }).waitFor();
    assert.equal(await page.locator('.agent-btn:visible').textContent(), selectedBefore, 'disclosure does not select the account');
    await empty.getByRole('button', { name: '刷新 codex2 的额度', exact: true }).waitFor();
    await page.keyboard.press('Escape');
    assert.equal(await empty.locator('.quota-quick-actions').count(), 0);
    assert.equal(await page.locator('.agent-picker-panel').count(), 1, 'Escape closes empty-quota details first');
    await fits(page);
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
    await page.evaluate(() => localStorage.removeItem('calli.quota.v1'));
    await page.reload();
    await page.unroute('**/api/agents/*/quota*');
    await page.route('**/api/agents/*/quota*', route => route.fulfill({ status: 503, json: { error: 'unavailable' } }));
    await openAccounts(page);
    await first.getByText('额度暂不可用', { exact: true }).waitFor();
    assert.equal(await page.getByRole('meter').count(), 0);
    assert.equal(await summary(first).count(), 0);
  } finally { await context.close(); }
});

test('missing quota stays one line through loading, refresh and failure in both themes and sizes', async () => {
  for (const width of [1400, 320]) for (const colorScheme of ['light', 'dark']) {
    const mobile = width < 640;
    const context = await browser.newContext({ viewport: { width, height: mobile ? 844 : 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    try {
      const page = await context.newPage(); page.setDefaultTimeout(10_000);
      let release;
      let hold = new Promise(resolve => { release = resolve; });
      let failed = false;
      await page.route('**/api/agents/*/quota*', async route => {
        await hold;
        await route.fulfill(failed ? { status: 503, json: { error: 'unavailable' } }
          : { json: { windows: [], checkedAt: new Date().toISOString() } });
      });
      await openUi(page, daemon);
      await openAccounts(page);
      const row = account(page, 'Codex 2');
      const empty = row.locator('.agent-quota-empty'), note = empty.locator('.agent-quota-note');
      await note.getByText('正在查询额度…', { exact: true }).waitFor();
      const loadingHeight = (await empty.boundingBox()).height;
      release();
      await note.getByText('未提供', { exact: true }).waitFor();
      const emptyHeight = (await empty.boundingBox()).height;
      assert.equal(emptyHeight, loadingHeight, 'the first read does not change row height');
      assert.ok(emptyHeight <= 32);
      assert.equal(await empty.locator('.quota-quick-actions').count(), 0);
      await fits(page);
      await shot(page, `agent-quota-empty-${width}-${colorScheme}`);
      await row.getByRole('menuitemradio').focus();
      await page.keyboard.press('Tab');
      assert.equal(await note.evaluate(el => el === document.activeElement), true);
      await page.keyboard.press('Enter');
      await empty.getByText('服务未提供额度信息', { exact: true }).waitFor();
      await shot(page, `agent-quota-empty-details-${width}-${colorScheme}`);
      hold = new Promise(resolve => { release = resolve; });
      failed = true;
      await empty.getByRole('button', { name: '刷新 codex2 的额度', exact: true }).click();
      await page.keyboard.press('Escape');
      await note.getByText('正在查询额度…', { exact: true }).waitFor();
      release();
      await note.getByText('额度暂不可用', { exact: true }).waitFor();
      assert.equal((await empty.boundingBox()).height, emptyHeight, 'errors keep the compact layout');
      assert.equal(await empty.getByRole('meter').count(), 0, 'missing usage is never reported as zero');
      await note.click();
      await empty.getByRole('button', { name: '刷新 codex2 的额度', exact: true }).getByText('重试', { exact: true }).waitFor();
      await fits(page);
    } finally { await context.close(); }
  }
});

test('narrow summaries keep exhaustion visible and expose every omitted pool on click', async () => {
  for (const locale of ['zh-CN', 'en-US', 'ja-JP']) {
    const context = await browser.newContext({ viewport: { width: 320, height: 700 }, isMobile: true, hasTouch: true, locale });
    try {
      const page = await context.newPage();
      page.setDefaultTimeout(10_000);
      // Connect in the harness language before switching the application locale.
      await page.addInitScript(() => localStorage.setItem('calli.locale', 'zh'));
      await openUi(page, daemon);
      await context.route('**/api/agents/*/quota*', route => route.fulfill({ json: {
        windows: Array.from({ length: 8 }, (_, i) => ({ label: i % 2 ? '7D' : '5H', scope: `Pool ${i + 1}`, usedPercent: i === 7 ? 100 : 10 })),
        checkedAt: new Date().toISOString(),
      } }));
      // Set the language after the init script, then reload without resetting it.
      await page.evaluate(value => localStorage.setItem('calli.locale', value), locale.split('-')[0]);
      // init scripts run on reload, so use a fresh page sharing the authenticated context.
      const translated = await context.newPage();
      await translated.goto(daemon.base);
      await page.close();
      await openAccounts(translated);
      const first = account(translated, 'Codex');
      await summary(first).getByText('0%', { exact: true }).waitFor();
      assert.match(await summary(first).innerText(), /\+\d+/, 'overflow is discoverable');
      await fits(translated);
      await summary(first).click();
      await first.getByRole('meter').first().waitFor();
      assert.equal(await first.getByRole('meter').count(), 8);
      await first.locator('.agent-quota-details').getByText('Pool 8', { exact: true }).waitFor();
      await shot(translated, `agent-quota-overflow-${locale}`);
    } finally { await context.close(); }
  }
});
