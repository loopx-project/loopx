import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { startDaemon, launchBrowser, api, openUi } from '../harness.mjs';

test('all provider failures use quiet expandable notices in the thread view', async () => {
  const daemon = await startDaemon({ LOOCHAT_RETRY_TICK_MS: '600000' }, {
    agents: [{ id: 'agy', adapter: 'agy', label: 'Agent 1', command: ['fake-agy'] }],
  });
  const browser = await launchBrowser();
  const shots = process.env.LOOCHAT_ERROR_SHOTS ?? join(tmpdir(), 'loochat-error-notice');
  mkdirSync(shots, { recursive: true });
  const request = async (path, method = 'GET', body) => {
    const res = await api(daemon, path, { method, ...(body ? { body: JSON.stringify(body) } : {}) });
    assert.ok(res.ok, `${path}: ${res.status}${res.ok ? '' : ': ' + await res.text()}`);
    return res.json();
  };
  const wait = async check => {
    const end = Date.now() + 30_000;
    while (!await check()) {
      assert.ok(Date.now() < end, 'timed out waiting for the fake turn');
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  };
  const samples = [
    { message: '529 {"error":{"type":"overloaded_error","message":"Overloaded"}}', summary: '服务繁忙' },
    { message: 'HTTP 429 rate_limit_exceeded', summary: '请求受限' },
    { message: 'HTTP 401 Incorrect API key provided', summary: '认证/访问失败' },
    { message: 'You have exhausted your quota on this model.', summary: '额度用尽' },
    { message: 'Unknown provider failure <script>throw new Error("unsafe")</script>', summary: '本轮出错' },
  ];
  try {
    const { id: threadId } = await request('/api/threads', 'POST', { agent: 'agy', cwd: daemon.workdir, title: 'Provider failures' });
    await request(`/api/threads/${threadId}/turns`, 'POST', { text: 'Check provider availability' });
    await wait(async () => (await request(`/api/threads/${threadId}`)).status === 'idle');
    await request('/api/handover-settings', 'PUT', { enabled: false, resume: false, threshold: 90, orders: {} });
    // The fake provider returns real errors through the adapter and daemon, with no live database writes.
    for (const [index, sample] of samples.entries()) {
      await request(`/api/threads/${threadId}/turns`, 'POST', {
        text: `provider-error: ${sample.message}`, queue: true, displayText: `检查服务状态 ${index + 1}`,
      });
      await wait(async () => (await request(`/api/threads/${threadId}/events`)).filter(event => event.type === 'turn.error').at(-1)?.data.message === sample.message);
    }

    const supportsRetry = 'autoRetry' in await request(`/api/threads/${threadId}`);
    for (const [name, viewport, theme] of [
      ['desktop-light', { width: 1440, height: 960 }, 'light'],
      ['desktop-dark', { width: 1440, height: 960 }, 'dark'],
      ['phone-light', { width: 390, height: 844 }, 'light'],
      ['phone-dark', { width: 390, height: 844 }, 'dark'],
    ]) {
      const page = await browser.newPage({ viewport, colorScheme: theme });
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await openUi(page, daemon);
      await page.evaluate(id => { location.hash = '#/thread/' + id; }, threadId);
      await page.locator('.thread .error-box').first().waitFor();
      for (const sample of samples) {
        const card = page.locator('.thread .error-box').filter({ has: page.locator('.error-heading', { hasText: sample.summary }) }).first();
        await card.waitFor();
        assert.equal(await card.locator('.error-toggle').getAttribute('aria-expanded'), 'false');
        assert.equal(await card.locator('.error-detail').textContent(), '', 'raw details are not built while closed');
        await card.locator('.error-toggle').focus();
        await page.keyboard.press('Enter');
        assert.ok((await card.locator('.error-detail').innerText()).includes(sample.message));
        assert.equal(await card.locator('.error-detail script').count(), 0, 'provider text stays text');
        await card.locator('.error-toggle').click();
      }
      // A real fake-agent failure checks the live retry state, not a mocked card.
      if (name === 'desktop-light') {
        await request(`/api/threads/${threadId}/turns`, 'POST', { text: 'provider-error: API error (attempt 2): UNAVAILABLE (code 503): No capacity available for model gemini-3.8-flash-high on the server', queue: true });
        if (supportsRetry) await wait(async () => Boolean((await request(`/api/threads/${threadId}`)).autoRetry));
      }
      const last = page.locator('.thread .error-box').last();
      await last.locator('.error-heading').filter({ hasText: /服务繁忙.*503/ }).waitFor();
      if (supportsRetry) await last.locator('.error-retry').waitFor();
      await last.scrollIntoViewIfNeeded();
      const initialComposer = await page.locator('.composer').boundingBox();
      await page.screenshot({ path: join(shots, `${name}-collapsed.png`), animations: 'disabled' });
      await last.locator('.error-toggle').click();
      assert.match(await last.innerText(), /API error \(attempt 2\).*503/);
      const expandedComposer = await page.locator('.composer').boundingBox();
      assert.equal(expandedComposer.y, initialComposer.y, 'expanding a detail keeps the composer fixed');
      await last.scrollIntoViewIfNeeded();
      await page.screenshot({ path: join(shots, `${name}-expanded.png`), animations: 'disabled' });
      assert.ok(await last.evaluate(el => el.scrollWidth <= el.clientWidth), 'long errors wrap on phones');
      assert.equal(await last.evaluate(el => getComputedStyle(el).backgroundColor), 'rgba(0, 0, 0, 0)');
      if (name === 'phone-dark' && supportsRetry) {
        await last.getByRole('button', { name: '取消自动重试', exact: true }).click();
        await last.locator('.error-retry').waitFor({ state: 'detached' });
        assert.equal((await request(`/api/threads/${threadId}`)).autoRetry, null);
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally { await browser.close(); await daemon.stop(); }
});
