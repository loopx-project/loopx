import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

test('compact references coexist with notes, survive reload/failure/queue editing, and work on four layouts', { timeout: 180000 }, async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  const shots = process.env.CALLI_REFERENCE_SHOTS;
  if (shots) mkdirSync(shots, { recursive: true });
  const post = async (path, body) => {
    const response = await api(daemon, path, { method: 'POST', body: JSON.stringify(body) });
    assert.ok(response.ok, `${path}: ${await response.clone().text()}`);
    return response.json();
  };
  const idle = async id => {
    for (let i = 0; i < 100; i++) {
      if ((await (await api(daemon, `/api/threads/${id}`)).json()).status !== 'running') return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error('agent did not finish');
  };
  try {
    const source = await post('/api/threads', { agent: 'pi', cwd: daemon.workdir, title: 'Reference source' });
    await post(`/api/threads/${source.id}/turns`, { text: 'Reference source question' });
    await idle(source.id);
    for (const mobile of [false, true]) for (const theme of ['light', 'dark']) {
      const thread = await post('/api/threads', { agent: 'pi', cwd: daemon.workdir, title: `References ${mobile} ${theme}` });
      await post(`/api/threads/${thread.id}/turns`, { text: 'Please explain the design.' });
      await idle(thread.id);
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await openUi(page, daemon);
      await page.evaluate(({ id, theme }) => { document.documentElement.dataset.theme = theme; location.hash = `#/thread/${id}`; }, { id: thread.id, theme });
      const composer = page.locator('.composer');
      const input = composer.locator('textarea').last();
      await page.getByRole('button', { name: '引用整条回复', exact: true }).last().click();
      await composer.locator('.note-strip').waitFor();
      await input.fill('Please use @');
      await page.locator('.reference-picker').waitFor();
      // One kind of reference at a time: folders wait in their own tab, not below the chats.
      assert.equal(await page.getByRole('tab').count(), 3);
      assert.equal(await page.getByRole('button', { name: '引用此目录', exact: true }).count(), 0);
      await page.locator('.reference-option', { hasText: 'Reference source' }).click();
      await composer.locator('.context-reference').waitFor();
      assert.equal(await input.inputValue(), 'Please use ');
      await composer.getByRole('button', { name: '引用会话或目录', exact: true }).click();
      await page.getByRole('tab', { name: /目录/ }).click();
      await page.getByRole('button', { name: '引用此目录', exact: true }).click();
      await composer.locator('.context-reference').nth(1).waitFor();
      assert.equal(await composer.locator('.context-reference').count(), 2);
      await input.fill('Compare these references and follow note 1.');
      await page.waitForTimeout(500);
      await page.reload();
      await page.locator('.sidebar-inner').waitFor({ state: 'attached' });
      await page.evaluate(id => { location.hash = `#/thread/${id}`; }, thread.id);
      await composer.locator('.context-reference').nth(1).waitFor();
      await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
      assert.equal(await composer.locator('.note-strip').count(), 1);
      await composer.locator('.context-reference button').first().click();
      await page.getByRole('dialog').waitFor();
      await page.waitForTimeout(250);
      assert.ok(await page.getByRole('dialog').evaluate(element => {
        const rect = element.getBoundingClientRect();
        return rect.top >= 0 && rect.bottom <= innerHeight && rect.left >= 0 && rect.right <= innerWidth;
      }), 'reference details stay inside the viewport');
      if (shots) await page.screenshot({ path: join(shots, `${mobile ? 'mobile' : 'desktop'}-${theme}-details.png`) });
      await page.getByRole('button', { name: '关闭', exact: true }).last().click();
      if (shots) await page.screenshot({ path: join(shots, `${mobile ? 'mobile' : 'desktop'}-${theme}-composer.png`) });
      await composer.getByRole('button', { name: '引用会话或目录', exact: true }).click();
      await page.locator('.reference-picker').waitFor();
      await page.getByRole('tab', { name: /目录/ }).click();
      await page.getByRole('button', { name: '引用此目录', exact: true }).waitFor();
      await page.waitForTimeout(250);
      if (shots) await page.screenshot({ path: join(shots, `${mobile ? 'mobile' : 'desktop'}-${theme}-picker.png`) });
      await page.keyboard.press('Escape');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      // A refused send must leave references and notes editable.
      await page.route('**/turns', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'test send failed' }) }), { times: 1 });
      await composer.locator('.send-btn:not(.stop)').click();
      await page.getByText('test send failed', { exact: true }).waitFor();
      await composer.locator('.context-reference').nth(1).waitFor();
      assert.equal(await composer.locator('.context-reference').count(), 2);
      assert.equal(await composer.locator('.note-strip').count(), 1);
      // Queue and take back the same message; structured UI must recover from the wire text.
      await post(`/api/threads/${thread.id}/turns`, { text: 'slow' });
      await composer.locator('.send-btn.stop').waitFor();
      await composer.locator('.send-btn:not(.stop)').click();
      await composer.locator('.queue-item').waitFor();
      assert.equal(await composer.locator('.context-reference').count(), 0);
      assert.ok(!(await composer.locator('.queue-item').innerText()).includes('calli-references'));
      await composer.locator('.queue-item').getByRole('button', { name: /编辑/ }).click();
      await composer.locator('.context-reference').nth(1).waitFor();
      assert.equal(await composer.locator('.note-strip').count(), 1);
      await composer.locator('.send-btn.stop').click();
      await idle(thread.id);
      await composer.locator('.send-btn:not(.stop)').click();
      await page.locator('.row.user .context-reference').nth(1).waitFor();
      assert.equal(await composer.locator('.note-strip').count(), 0);
      const events = await (await api(daemon, `/api/threads/${thread.id}/events`)).json();
      const started = events.findLast(event => event.type === 'turn.started');
      assert.match(started.data.text, /calli references/);
      assert.match(started.data.text, /\[注释 1\]/);
      assert.ok(!started.data.displayText.includes('calli-reference-reader'));
      await idle(thread.id);
      await page.locator('.row.user').last().getByRole('button', { name: '编辑这条消息', exact: true }).click();
      const edit = page.locator('.message-edit');
      assert.equal(await edit.locator('.context-reference').count(), 2);
      assert.ok(!(await edit.locator('textarea').inputValue()).includes('calli-references'));
      await edit.getByRole('button', { name: '取消', exact: true }).click();
      assert.deepEqual(errors, []);
      await context.close();
    }
  } finally { await browser.close(); await daemon.stop(); }
});
