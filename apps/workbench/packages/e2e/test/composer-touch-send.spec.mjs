import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon;
let browser;
before(async () => { daemon = await startDaemon(); browser = await launchBrowser(); });
after(async () => { await browser?.close(); await daemon?.stop(); });

/** Each case gets an idle fake-agent thread and a fresh draft. */
async function withComposer(run, { mobile = true, colorScheme = 'light' } = {}) {
  const title = `输入法发送 ${Date.now()}-${colorScheme}`;
  const created = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title }) });
  assert.ok(created.ok, await created.clone().text());
  const thread = await created.json();
  const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
  try {
    const page = await context.newPage();
    await openUi(page, daemon);
    if (mobile) await page.locator('.menu-btn').tap();
    await page.locator('.thread-row').filter({ hasText: title }).click();
    const requests = [];
    const turns = `**/api/threads/${thread.id}/turns`;
    page.on('request', request => {
      if (request.method() === 'POST' && request.url().endsWith(`/api/threads/${thread.id}/turns`)) requests.push(request.postDataJSON());
    });
    await run({ page, input: page.locator('.composer textarea'), send: page.locator('.composer .send-btn:not(.stop)'), requests, turns });
  } finally { await context.close(); }
}

/** Headless Chromium has no OS keyboard; shrink the visual viewport as a phone keyboard does. */
async function keyboardHeight(page, height) {
  await page.evaluate(height => {
    const vv = window.visualViewport;
    Object.defineProperty(vv, 'height', { configurable: true, get: () => height });
    vv.dispatchEvent(new Event('resize'));
  }, height);
}

/** Model an IME whose final input follows compositionend, including a native value change. */
async function composingDraft(input, finalText, commitOnBlur = false) {
  await input.focus();
  await input.evaluate((el, { finalText, commitOnBlur }) => {
    window.imeEvents = [];
    const setValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
    window.commitIme = () => {
      window.imeEvents.push('compositionend');
      el.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: finalText }));
      // The trailing input must reach React before the retained tap reads its submit callback.
      setValue.call(el, finalText);
      el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: finalText, isComposing: false }));
      window.imeEvents.push('final-input');
    };
    if (commitOnBlur) el.addEventListener('blur', () => {
      window.imeEvents.push('blur');
      if (commitOnBlur === true) queueMicrotask(window.commitIme);
    }, { once: true });
    document.addEventListener('pointerup', () => window.imeEvents.push('release'), { once: true, capture: true });
    el.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
    setValue.call(el, 'zhongwen');
    el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertCompositionText', data: 'zhongwen', isComposing: true }));
  }, { finalText, commitOnBlur });
}

async function holdSend(page, turns) {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route(turns, async route => { await gate; await route.continue().catch(() => undefined); });
  return release;
}

test('touching send submits even when the keyboard blur swallows the click', async () => {
  await withComposer(async ({ page, input, send, requests }) => {
    await page.evaluate(() => {
      document.addEventListener('pointerdown', event => {
        const button = event.target instanceof Element ? event.target.closest('.composer .send-btn:not(.stop)') : null;
        if (!button) return;
        // Some mobile browsers first let the focused input lose focus, which closes the keyboard.
        if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
      }, true);
      document.addEventListener('click', event => {
        const button = event.target instanceof Element ? event.target.closest('.composer .send-btn:not(.stop)') : null;
        if (!button) return;
        event.preventDefault();
        event.stopImmediatePropagation();
      }, true);
    });
    await input.tap();
    await input.fill('mobile first tap send');
    await send.tap();
    await page.getByText('hi there mobile first tap send').waitFor({ timeout: 15_000 });
    assert.deepEqual(requests.map(r => r.text), ['mobile first tap send']);
    assert.equal(await input.inputValue(), '');
  });
});

for (const colorScheme of ['light', 'dark']) test(`finalized native Chinese sends once with the keyboard up and keeps focus (${colorScheme})`, async () => {
  await withComposer(async ({ page, input, send, requests }) => {
    await input.tap();
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Input.imeSetComposition', { text: 'zhongwen', selectionStart: 8, selectionEnd: 8 });
    await cdp.send('Input.insertText', { text: '中文消息' });
    await keyboardHeight(page, 500);
    await send.tap();
    await page.getByText('hi there 中文消息').waitFor({ timeout: 15_000 });
    await page.waitForTimeout(550);
    assert.deepEqual(requests.map(r => r.text), ['中文消息']);
    assert.equal(await input.inputValue(), '');
    assert.ok(await input.evaluate(el => document.activeElement === el));
    await cdp.detach();
  }, { colorScheme });
});

test('one tap finishes active composition after release and sends the trailing final input once', async () => {
  await withComposer(async ({ page, input, send, requests, turns }) => {
    const release = await holdSend(page, turns);
    try {
      await composingDraft(input, '中文已定稿', 'manual');
      await keyboardHeight(page, 500);
      await send.tap();
      await page.waitForTimeout(100);
      assert.deepEqual(requests, [], 'no request may snapshot text before compositionend and final input');
      assert.deepEqual(await page.evaluate(() => window.imeEvents), ['release', 'blur']);
      const sent = page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith('/turns'));
      await page.evaluate(() => window.commitIme());
      await sent;
      assert.deepEqual(requests.map(r => r.text), ['中文已定稿'], 'the same tap sends final Chinese, never interim spelling');
      assert.deepEqual(await page.evaluate(() => window.imeEvents), ['release', 'blur', 'compositionend', 'final-input']);
      await send.dispatchEvent('click', { detail: 1 });
    } finally { release(); }
    await page.getByText('hi there 中文已定稿').waitFor({ timeout: 15_000 });
    await page.waitForTimeout(550);
    assert.deepEqual(requests.map(r => r.text), ['中文已定稿']);
    assert.equal(await input.inputValue(), '');
    assert.ok(await input.evaluate(el => document.activeElement === el));
  });
});

test('active native composition finishes on the same tap even when compositionend has no trailing input', async () => {
  await withComposer(async ({ page, input, send, requests, turns }) => {
    const release = await holdSend(page, turns);
    try {
      await input.tap();
      const cdp = await page.context().newCDPSession(page);
      await input.evaluate(el => {
        window.nativeImeEnded = false;
        el.addEventListener('compositionend', () => { window.nativeImeEnded = true; }, { once: true });
      });
      await cdp.send('Input.imeSetComposition', { text: '中文候选', selectionStart: 4, selectionEnd: 4 });
      await send.tap();
      await page.waitForTimeout(100);
      assert.ok(await page.evaluate(() => window.nativeImeEnded), 'send explicitly terminates the active native composition');
      assert.deepEqual(requests.map(r => r.text), ['中文候选']);
      await cdp.detach();
    } finally { release(); }
    await page.getByText('hi there 中文候选').waitFor({ timeout: 15_000 });
    assert.equal(await input.inputValue(), '');
    assert.ok(await input.evaluate(el => document.activeElement === el));
  });
});

test('a slow successful send preserves Chinese committed while the request was pending', async () => {
  await withComposer(async ({ page, input, send, requests, turns }) => {
    const release = await holdSend(page, turns);
    const completed = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/turns'));
    try {
      await input.fill('first finalized message');
      await send.tap();
      await page.waitForTimeout(100);
      assert.deepEqual(requests.map(r => r.text), ['first finalized message']);
      await composingDraft(input, '稍后提交的中文');
      await page.evaluate(() => window.commitIme());
    } finally { release(); }
    await completed;
    await page.getByText('hi there first finalized message').waitFor({ timeout: 15_000 });
    await page.waitForTimeout(100);
    assert.equal(await input.inputValue(), '稍后提交的中文', 'an earlier send must not clear a later IME commit');
    const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('calli.drafts') ?? '{}'));
    assert.ok(Object.values(stored).some(draft => draft.text === '稍后提交的中文'), 'the surviving draft is durable too');
  });
});

test('a failed send from an active composition keeps the finalized draft for retry', async () => {
  await withComposer(async ({ page, input, send, requests, turns }) => {
    await page.route(turns, route => route.fulfill({ status: 503, json: { error: 'fake send failed' } }));
    await composingDraft(input, '失败后保留中文', true);
    await send.tap();
    await page.getByText('fake send failed', { exact: true }).waitFor();
    assert.deepEqual(requests.map(r => r.text), ['失败后保留中文']);
    assert.equal(await input.inputValue(), '失败后保留中文');
    assert.ok(await input.evaluate(el => document.activeElement === el));
  });
});

for (const ending of ['slide', 'cancel']) test(`a previous suppression timer cannot send a later ${ending} gesture through a compatibility click`, async () => {
  await withComposer(async ({ page, input, send, requests }) => {
    await input.fill('gesture draft');
    await page.clock.install({ time: new Date('2026-01-01T00:00:00Z') });
    await page.clock.pauseAt(new Date('2026-01-01T00:00:01Z'));
    await send.evaluate(button => {
      // Synthetic pointer IDs let the two gestures and old timeout overlap deterministically.
      button.setPointerCapture = () => undefined;
      button.hasPointerCapture = () => false;
      window.sendGesture = (type, pointerId, offset = 0) => {
        const rect = button.getBoundingClientRect();
        button.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerType: 'touch', pointerId,
          clientX: rect.x + rect.width / 2 + offset, clientY: rect.y + rect.height / 2 }));
      };
    });
    await page.evaluate(() => {
      window.sendGesture('pointerdown', 1);
      window.sendGesture('pointermove', 1, 20);
      window.sendGesture('pointerup', 1);
    });
    await page.clock.runFor(400);
    await page.evaluate(ending => {
      window.sendGesture('pointerdown', 2);
      if (ending === 'slide') {
        window.sendGesture('pointermove', 2, 20);
        window.sendGesture('pointerup', 2);
      } else window.sendGesture('pointercancel', 2);
    }, ending);
    await page.clock.runFor(100);
    await send.dispatchEvent('click', { detail: 1 });
    await page.waitForTimeout(100);
    assert.deepEqual(requests, [], 'a canceled or slid gesture and its compatibility click send nothing');
    assert.equal(await input.inputValue(), 'gesture draft');
    // Keyboard and assistive activation have detail=0 and remain available during suppression.
    await send.dispatchEvent('click', { detail: 0 });
    await page.waitForTimeout(100);
    assert.deepEqual(requests.map(r => r.text), ['gesture draft']);
  });
});

test('tapping send on a phone while a turn is running queues the message without hitting stop or adjacent controls', async () => {
  const title = `运行中发送 ${Date.now()}`;
  const created = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title }) });
  assert.ok(created.ok, await created.clone().text());
  const thread = await created.json();
  await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'slow' }) });
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  try {
    const page = await context.newPage();
    let cancels = 0;
    const queued = [];
    page.on('request', request => {
      if (request.method() !== 'POST') return;
      if (request.url().endsWith(`/api/threads/${thread.id}/cancel`)) cancels++;
      if (request.url().endsWith(`/api/threads/${thread.id}/turns`)) queued.push(request.postDataJSON());
    });
    await openUi(page, daemon);
    await page.locator('.menu-btn').tap();
    await page.locator('.thread-row').filter({ hasText: title }).click();
    const stop = page.locator('.composer .send-btn.stop');
    const send = page.locator('.composer .send-btn:not(.stop)');
    const input = page.locator('.composer textarea');
    await stop.waitFor();
    await input.tap();
    const stopEmpty = await stop.boundingBox();
    await input.fill('继续追问');
    await send.waitFor();
    const sendTyped = await send.boundingBox();
    const stopTyped = await stop.boundingBox();
    assert.ok(sendTyped && stopTyped && stopEmpty, 'both composer buttons are measurable');
    assert.ok(stopTyped.x + stopTyped.width <= sendTyped.x + 1, 'stop sits to the left of queue send');
    assert.ok(Math.abs(sendTyped.x - stopEmpty.x) <= 1, 'typing a draft places queue send in the corner');

    await send.tap();
    await page.locator('.queue-item', { hasText: '继续追问' }).waitFor();
    await page.waitForTimeout(250);
    assert.equal(cancels, 0, 'the compatibility click from tapping send must not hit stop even as it slides back');
    assert.equal(await page.locator('.model-control-panel, .pop-panel').count(), 0, 'adjacent bar controls sliding left must not receive the compatibility click');
    assert.deepEqual(queued.map(r => r.text), ['继续追问']);
    const stopQueued = await stop.boundingBox();
    assert.ok(stopQueued && Math.abs(stopQueued.x - stopEmpty.x) <= 1, 'stop returns to the corner after send unmounts');

    // A separate deliberate tap on stop right away (still within the 500ms suppression window) works.
    await stop.tap();
    await send.waitFor();
    assert.equal(cancels, 1, 'an explicit tap on stop cancels the running turn');
    assert.equal(await input.inputValue(), '继续追问', 'stopping restores the queued draft to the box');
  } finally {
    await api(daemon, `/api/threads/${thread.id}/cancel`, { method: 'POST' });
    await context.close();
  }
});

test('desktop Enter keeps native IME guards and Shift+Enter behavior', async () => {
  await withComposer(async ({ page, input, requests }) => {
    await input.fill('desktop 中文');
    await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true });
    await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 229 });
    await page.waitForTimeout(100);
    assert.deepEqual(requests, []);
    await input.press('Shift+Enter');
    assert.equal(await input.inputValue(), 'desktop 中文\n');
    await input.press('Control+Enter');
    await page.getByText('hi there desktop 中文').waitFor({ timeout: 15_000 });
    assert.deepEqual(requests.map(r => r.text), ['desktop 中文']);
  }, { mobile: false });
});
