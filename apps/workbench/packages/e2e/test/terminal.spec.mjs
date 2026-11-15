// The web terminal: opens from the thread header (desktop) and the thread menu (phone), runs a
// real shell on the host, survives its panel being closed and reopened, and ends cleanly.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon;
let browser;

before(async () => {
  // /bin/sh keeps the test off the user's rc files and shell prompt.
  daemon = await startDaemon({ CALLI_TERM_SHELL: '/bin/sh' });
  browser = await launchBrowser();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
});

/** Waits until the rendered terminal text contains `needle`. */
async function termShows(page, needle, timeout = 15_000) {
  await page.waitForFunction(
    (n) => document.querySelector('.term-body')?.textContent?.includes(n) ?? false,
    needle,
    { timeout },
  );
}

async function openThreadWithTerminalButton(page, title) {
  await openUi(page, daemon);
  const thread = await (
    await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title }) })
  ).json();
  // On a phone the list is a page of its own, behind the menu button.
  if (await page.locator('.menu-btn').isVisible()) await page.locator('.menu-btn').click();
  await page.locator('.thread-item', { hasText: title }).click();
  return thread;
}

test('the header button opens a terminal that runs commands and survives closing', async () => {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  try {
    await openThreadWithTerminalButton(page, '终端桌面');
    await page.getByLabel('打开终端').click();
    await page.locator('.term-panel').waitFor();

    await page.locator('.term-body').click();
    await page.keyboard.type('echo e2e-ok');
    await page.keyboard.press('Enter');
    await termShows(page, 'e2e-ok');

    // A program asks the terminal for its background colour and reads the answer.
    const typed = [];
    page.on('request', (r) => { if (r.method() === 'POST' && /\/terminals\/[^/]+\/input$/.test(r.url())) typed.push(JSON.parse(r.postData()).data); });
    await page.keyboard.type(`stty raw -echo; printf '\\033]11;?\\033\\\\'; timeout 1 cat >/dev/null; stty sane; echo asked-$((1+1))`);
    await page.keyboard.press('Enter');
    await termShows(page, 'asked-2');
    await page.waitForTimeout(300);
    typed.length = 0; // the live answer above was asked for

    // Closing the panel hides the terminal; reopening reattaches and replays what already ran.
    await page.getByLabel('关闭').click();
    assert.equal(await page.locator('.term-panel').count(), 0);
    await page.getByLabel('打开终端').click();
    await page.locator('.term-panel').waitFor();
    await termShows(page, 'e2e-ok');
    // The replay passes the old question through xterm again; its answer must not reach the shell.
    await page.waitForTimeout(800);
    assert.deepEqual(typed.filter((d) => d.includes('rgb:')), [], 'no answer to an old query was typed');

    // Another page attached to the same terminal, not in use, sees the same question: only the
    // page in use answers, or the second answer lands at the prompt as typing.
    // A context of its own: two pages' long-lived streams in one would use up its connections.
    const otherCtx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const other = await otherCtx.newPage();
    const otherTyped = [];
    other.on('request', (r) => { if (r.method() === 'POST' && /\/terminals\/[^/]+\/input$/.test(r.url())) otherTyped.push(JSON.parse(r.postData()).data); });
    await openThreadWithTerminalButton(other, '终端旁观');
    await other.getByLabel('打开终端').click();
    await other.locator('.term-panel').waitFor();
    await other.evaluate(() => { document.hasFocus = () => false; });
    await page.bringToFront();
    await page.locator('.term-body').click();
    await page.keyboard.type(`stty raw -echo; printf '\\033]11;?\\033\\\\'; timeout 1 cat >/dev/null; stty sane; echo again-$((2+1))`);
    await page.keyboard.press('Enter');
    await termShows(page, 'again-3');
    await page.waitForTimeout(300);
    assert.equal(typed.filter((d) => d.includes('rgb:')).length, 1, 'the page in use answers');
    assert.deepEqual(otherTyped.filter((d) => d.includes('rgb:')), [], 'the other page keeps quiet');
    await otherCtx.close();
    assert.equal(await page.locator('.term-note').count(), 0, 'the reopened terminal is live, not ended');

    // Exiting the shell ends the panel's session and offers a fresh one.
    await page.locator('.term-body').click();
    await page.keyboard.type('exit');
    await page.keyboard.press('Enter');
    await page.locator('.term-note', { hasText: '终端已结束' }).waitFor();
    await page.getByRole('button', { name: '重新打开' }).click();
    await page.locator('.term-note').waitFor({ state: 'detached' });
    await page.locator('.term-body').click();
    await page.keyboard.type('echo again-ok');
    await page.keyboard.press('Enter');
    await termShows(page, 'again-ok');
  } finally {
    await ctx.close();
  }
});

test('on a phone the terminal opens from the thread menu', async () => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await ctx.newPage();
  try {
    await openThreadWithTerminalButton(page, '终端手机');
    await page.getByLabel('这个线程的更多操作').click();
    await page.getByRole('menuitem', { name: '打开终端' }).click();
    await page.locator('.term-panel').waitFor();

    // A phone types into a field of its own, a line at a time; the terminal's own field never
    // brings up the keyboard (iOS keyboards lose track of it).
    assert.equal(await page.locator('.term-body textarea').getAttribute('inputmode'), 'none');
    const field = page.getByLabel('终端输入');
    await field.fill('echo m-ok');
    await field.press('Enter');
    await termShows(page, 'm-ok');
    assert.equal(await field.inputValue(), '');
    await field.fill('echo m-again');
    await page.locator('.term-send').click();
    await termShows(page, 'm-again');

    // The keys a phone keyboard lacks go to the terminal as the bytes a keyboard would send.
    const typed = [];
    page.on('request', (r) => { if (r.method() === 'POST' && /\/terminals\/[^/]+\/input$/.test(r.url())) typed.push(JSON.parse(r.postData()).data); });
    for (const key of ['Esc', 'Tab', 'Ctrl+C', '上', '左']) {
      await page.getByRole('button', { name: key, exact: true }).click();
      await page.waitForTimeout(60);
    }
    await page.waitForTimeout(200);
    assert.deepEqual(typed, ['\x1b', '\t', '\x03', '\x1b[A', '\x1b[D']);
  } finally {
    await ctx.close();
  }
});

test('the slash menu opens the terminal', async () => {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  try {
    await openThreadWithTerminalButton(page, '终端斜杠');
    await page.locator('.composer textarea').click();
    await page.keyboard.type('/终端');
    await page.getByRole('option', { name: /终端/ }).click();
    await page.locator('.term-panel').waitFor();
    await termShows(page, '$');
  } finally {
    await ctx.close();
  }
});
