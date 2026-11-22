import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, openUi, launchBrowser, api } from '../harness.mjs';

// Native copy action, synthetic paste delivery, real daemon and fake model. No OS clipboard claim.
test('copied references paste as durable notes with scoped provenance on four layouts', { timeout: 180000 }, async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  const post = async (path, body) => {
    const response = await api(daemon, path, { method: 'POST', body: JSON.stringify(body) });
    assert.ok(response.ok, await response.clone().text()); return response.json();
  };
  const idle = async id => {
    for (let n = 0; n < 120; n++) {
      if ((await (await api(daemon, `/api/threads/${id}`)).json()).status === 'idle') return;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error('turn did not finish');
  };
  const paste = async (input, text) => input.evaluate((element, text) => {
    const data = new DataTransfer(); data.setData('text/plain', text);
    element.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
  }, text);
  try {
    for (const mobile of [false, true]) for (const theme of ['light', 'dark']) {
      const source = await post('/api/threads', { agent: 'pi', cwd: daemon.workdir, title: 'Quote source' });
      const target = await post('/api/threads', { agent: 'pi', cwd: daemon.workdir, title: 'Quote target' });
      await post(`/api/threads/${source.id}/turns`, { text: 'A passage with **bold** and a second sentence.' }); await idle(source.id);
      await post(`/api/threads/${target.id}/turns`, { text: 'target-ready' }); await idle(target.id);
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme: theme });
      try {
        const page = await context.newPage(), errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await openUi(page, daemon);
        await page.evaluate(id => { location.hash = `#/thread/${id}`; }, source.id);
        await page.locator('.main .thread .markdown').filter({ hasText: 'A passage with' }).waitFor();
        await page.getByRole('button', { name: '更多消息操作', exact: true }).last().click();
        await page.getByRole('menuitem', { name: '复制为引用', exact: true }).click();
        const copied = await page.evaluate(() => JSON.parse(localStorage.getItem('calli.copiedQuote')));
        assert.equal(copied.threadId, source.id); assert.equal(copied.host, null);
        await page.getByRole('button', { name: '更多消息操作', exact: true }).last().click();
        await page.getByRole('menuitem', { name: '复制回答', exact: true }).click();
        await page.waitForFunction(() => localStorage.getItem('calli.copiedQuote') === null);
        await page.getByRole('button', { name: '更多消息操作', exact: true }).last().click();
        await page.getByRole('menuitem', { name: '复制为引用', exact: true }).click();
        const text = copied.text.trim().split('\n').map(line => `> ${line}`.trimEnd()).join('\n');
        await page.evaluate(id => { location.hash = `#/thread/${id}`; }, target.id);
        await page.getByText('hi there target-ready', { exact: true }).waitFor();
        const composer = page.locator('.composer'), input = composer.locator('textarea').last();
        await input.fill('My existing draft'); await paste(input, text);
        await composer.locator('.note-strip').waitFor();
        assert.equal(await input.inputValue(), 'My existing draft');
        assert.equal(await composer.locator('.note-strip').count(), 1);
        await page.waitForFunction(id => { const draft = JSON.parse(localStorage.getItem('calli.drafts') ?? '{}')[id]; return draft?.text === 'My existing draft' && draft.notes?.length === 1; }, target.id);
        await page.reload();
        await page.locator('.sidebar-inner').waitFor({ state: 'attached' });
        await page.evaluate(id => { location.hash = `#/thread/${id}`; }, target.id);
        await composer.locator('.note-strip').waitFor();
        assert.equal(await input.inputValue(), 'My existing draft');
        if (process.env.LOOCHAT_E2E_SHOTS_DIR) {
          mkdirSync(process.env.LOOCHAT_E2E_SHOTS_DIR, { recursive: true });
          await page.screenshot({ path: join(process.env.LOOCHAT_E2E_SHOTS_DIR, `pasted-quote-${mobile ? 'phone' : 'desktop'}-${theme}.png`), animations: 'disabled' });
        }
        const sentResponse = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === `/api/threads/${target.id}/turns`);
        await composer.locator('.send-btn:not(.stop)').click(); await sentResponse; await idle(target.id);
        await composer.locator('.note-strip').waitFor({ state: 'detached' });
        const events = await (await api(daemon, `/api/threads/${target.id}/events`)).json();
        const sent = events.findLast(event => event.type === 'turn.started').data.text;
        assert.ok(sent.includes(`来源：另一个会话，原文和上下文用 loochat history ${source.id} --turn ${copied.turnId} 读`));
        assert.match(sent, /My existing draft/);
        assert.equal((await (await api(daemon, `/api/threads/${source.id}/events`)).json()).filter(e => e.type === 'turn.started').length, 1);
        // The same ids on a different machine must not grant local provenance.
        await page.evaluate(copied => localStorage.setItem('calli.copiedQuote', JSON.stringify({ ...copied, host: 'another-machine' })), copied);
        await paste(input, text); await composer.locator('.note-strip').waitFor();
        await input.fill('Cross-machine quote');
        const crossResponse = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === `/api/threads/${target.id}/turns`);
        await composer.locator('.send-btn:not(.stop)').click(); await crossResponse; await idle(target.id);
        const cross = (await (await api(daemon, `/api/threads/${target.id}/events`)).json()).findLast(e => e.type === 'turn.started').data.text;
        assert.match(cross, /\[注释 1\]/); assert.ok(!cross.includes('loochat history'));
        assert.deepEqual(errors, []);
      } finally { await context.close(); }
    }
  } finally { await browser.close(); await daemon.stop(); }
});
