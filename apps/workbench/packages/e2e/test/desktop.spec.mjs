// The remote desktop, opened from the sidebar: a real Xvnc started for the test, seen through the daemon's WebSocket and
// noVNC. It opens ready to control: on a computer a hover moves the machine's cursor and typing goes
// straight in, on a phone it opens ready to touch, type, copy and
// paste. The VNC password is asked once and kept on the device.
// Skipped where Xvnc is not installed.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { connect } from 'node:net';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

const which = (cmd) => {
  try {
    return execFileSync('sh', ['-c', `command -v ${cmd}`], { encoding: 'utf8' }).trim() || null;
  } catch {
    return null;
  }
};
const xvnc = which('Xvnc');
const xterm = which('xterm');
const vncpasswd = which('vncpasswd');
const xclip = which('xclip');
const skip = !xvnc || !xterm || !vncpasswd ? 'needs Xvnc, xterm and vncpasswd' : false;
const skipClip = skip || (!xclip ? 'needs xclip' : false);
const xev = which('xev');
const skipTouch = skip || (!xev ? 'needs xev' : false);

let dir;
const children = [];
let open; // a desktop without a password, with an xterm that writes the line it reads
let locked; // a desktop with a VNC password
let daemonOpen;
let daemonLocked;
let daemonNone;
let blocked; // a desktop with a password that has turned this address away
let daemonBlocked;
let touchy; // a bare desktop whose clicks xev reports, for the touch gestures
let daemonTouchy;
let phone; // a desktop with an xterm that keeps every line typed, for the phone's keyboard and clipboard
let daemonPhone;
/** What xev saw on the bare desktop's root window, as it comes: buttons, and separately keys. */
let xevOut = '';
let xevKeys = '';
let browser;

/** A display number nobody uses, so the test never touches a real desktop. */
function freeDisplay(from) {
  for (let n = from; n < from + 100; n++) if (!existsSync(`/tmp/.X11-unix/X${n}`) && !existsSync(`/tmp/.X${n}-lock`)) return n;
  throw new Error('no free display');
}

async function startXvnc(display, security) {
  const port = 5900 + display;
  const child = spawn(xvnc, [`:${display}`, '-rfbport', String(port), '-localhost', '-geometry', '1024x640', '-depth', '24', ...security], { stdio: 'ignore' });
  children.push(child);
  for (let i = 0; i < 100 && !existsSync(`/tmp/.X11-unix/X${display}`); i++) await new Promise((r) => setTimeout(r, 50));
  return { display, port, pid: child.pid };
}

before(async () => {
  if (skip) return;
  dir = mkdtempSync(join(tmpdir(), 'calli-e2e-desktop-'));
  open = await startXvnc(freeDisplay(70), ['-SecurityTypes', 'None']);
  const passwd = join(dir, 'passwd');
  writeFileSync(passwd, execFileSync(vncpasswd, ['-f'], { input: 'secret12\n' }));
  locked = await startXvnc(freeDisplay(open.display + 1), ['-SecurityTypes', 'VncAuth', '-PasswordFile', passwd]);
  blocked = await startXvnc(freeDisplay(locked.display + 1), ['-SecurityTypes', 'VncAuth', '-PasswordFile', passwd]);
  // Without a window manager, keys go to the window under the pointer: the xterm fills the screen.
  const out = join(dir, 'typed.txt');
  children.push(spawn(xterm, ['-geometry', '200x60+0+0', '-e', 'sh', '-c', `read line; printf '%s' "$line" > ${out}; sleep 600`], { env: { ...process.env, DISPLAY: `:${open.display}` }, stdio: 'ignore' }));
  open.out = out;
  daemonOpen = await startDaemon({ CALLI_DESKTOP_VNC: `127.0.0.1:${open.port}` });
  daemonLocked = await startDaemon({ CALLI_DESKTOP_VNC: `127.0.0.1:${locked.port}` });
  daemonNone = await startDaemon({ CALLI_DESKTOP_VNC: 'off' });
  daemonBlocked = await startDaemon({ CALLI_DESKTOP_VNC: `127.0.0.1:${blocked.port}` });
  if (xev) {
    touchy = await startXvnc(freeDisplay(blocked.display + 1), ['-SecurityTypes', 'None']);
    const watcher = spawn('stdbuf', ['-oL', xev, '-root', '-event', 'button'], { env: { ...process.env, DISPLAY: `:${touchy.display}` }, stdio: ['ignore', 'pipe', 'ignore'] });
    children.push(watcher);
    watcher.stdout.on('data', (d) => (xevOut += d));
    // Without a window manager, keys go to the window under the pointer: on a bare desktop, the root.
    const keys = spawn('stdbuf', ['-oL', xev, '-root', '-event', 'keyboard'], { env: { ...process.env, DISPLAY: `:${touchy.display}` }, stdio: ['ignore', 'pipe', 'ignore'] });
    children.push(keys);
    keys.stdout.on('data', (d) => (xevKeys += d));
    daemonTouchy = await startDaemon({ CALLI_DESKTOP_VNC: `127.0.0.1:${touchy.port}` });
  }
  if (xclip) {
    phone = await startXvnc(freeDisplay((touchy?.display ?? blocked.display) + 1), ['-SecurityTypes', 'None']);
    phone.out = join(dir, 'phone.txt');
    // A word to select at the top left; Ctrl+C from the copy button must not end the loop.
    const script = `printf 'copyme\\n'; trap '' INT; while IFS= read -r line; do printf '%s\\n' "$line" >> ${phone.out}; done`;
    children.push(spawn(xterm, ['-geometry', '200x60+0+0', '-e', 'sh', '-c', script], { env: { ...process.env, DISPLAY: `:${phone.display}` }, stdio: 'ignore' }));
    daemonPhone = await startDaemon({ CALLI_DESKTOP_VNC: `127.0.0.1:${phone.port}` });
  }
  browser = await launchBrowser();
});

after(async () => {
  await browser?.close();
  await daemonOpen?.stop();
  await daemonLocked?.stop();
  await daemonNone?.stop();
  await daemonBlocked?.stop();
  await daemonTouchy?.stop();
  await daemonPhone?.stop();
  for (const child of children) child.kill('SIGTERM');
  if (dir) rmSync(dir, { recursive: true, force: true });
});

async function openThread(page, daemon, title) {
  await openUi(page, daemon);
  await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title }) });
  if (await page.locator('.menu-btn').isVisible()) await page.locator('.menu-btn').click();
  await page.locator('.thread-item', { hasText: title }).click();
}

/**
 * The desktop is the machine's, not a thread's: it opens from the sidebar, behind the menu on a phone. Its row waits
 * behind the nav's "more" button, and has one of its own only while minimized and still connected.
 */
async function openDesktop(page) {
  if (await page.locator('.menu-btn').isVisible()) await page.locator('.menu-btn').click();
  await page.locator('.side-more').waitFor();
  const row = page.locator('.side-nav .side-link', { hasText: '桌面' });
  if (await row.count()) await row.click();
  else {
    await page.locator('.side-more').click();
    await page.getByRole('menuitem', { name: '桌面', exact: true }).click();
  }
}

const connected = (page) => page.locator('.desktop-screen canvas').waitFor({ timeout: 15_000 }).then(() => page.locator('.desktop-panel .term-note').waitFor({ state: 'detached', timeout: 15_000 }));

test('a machine without a desktop offers none', { skip }, async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  try {
    await openThread(page, daemonNone, '没有桌面');
    await page.locator('.side-more').click();
    await page.getByRole('menuitem').first().waitFor();
    assert.equal(await page.getByRole('menuitem', { name: '桌面', exact: true }).count(), 0);
  } finally {
    await page.close();
  }
});

test('the desktop opens ready to control; Control steps back to just watching', { skip }, async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  try {
    await openThread(page, daemonOpen, '桌面');
    await page.screenshot({ path: shot('desktop-sidebar') });
    await openDesktop(page);
    await connected(page);
    const control = page.locator('.desktop-control');
    assert.equal(await control.getAttribute('aria-pressed'), 'true');
    // The machine as the hub names it, not the VNC server's user@host.
    assert.equal((await page.locator('.desktop-name').innerText()).trim(), '这台电脑');
    const box = await page.locator('.desktop-screen canvas').boundingBox();
    assert.ok(box && box.width > 300 && box.height > 150, 'the picture is scaled into the panel');

    // Control: a real keyboard types straight onto the desktop, without a click into it first:
    // no typing bar, no keyboard button.
    assert.equal(await page.locator('.desktop-input').count(), 0);
    assert.equal(await page.getByLabel('键盘').count(), 0);
    await page.keyboard.type('hello-desktop');
    await page.keyboard.press('Enter');
    await waitForFile(open.out);
    assert.equal(readFileSync(open.out, 'utf8'), 'hello-desktop');

    // Control off: clicking and typing on the picture send nothing.
    await control.click();
    assert.equal(await control.getAttribute('aria-pressed'), 'false');
    await page.locator('.desktop-screen').click();
    await page.keyboard.type('nope');
    await page.keyboard.press('Enter');
    await page.waitForTimeout(500);
    assert.equal(readFileSync(open.out, 'utf8'), 'hello-desktop');
    await control.click();
    assert.equal(await control.getAttribute('aria-pressed'), 'true');
    for (const dark of [false, true]) {
      await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light' });
      await page.screenshot({ path: shot(`desktop-${dark ? 'dark' : 'light'}`) });
    }
  } finally {
    await page.close();
  }
});

test('minimizing keeps the connection: back from the sidebar without reconnecting', { skip }, async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  try {
    const sockets = [];
    page.on('websocket', (ws) => { if (ws.url().includes('/desktop/vnc')) sockets.push(ws); });
    await openUi(page, daemonOpen);
    await openDesktop(page);
    await connected(page);

    await page.getByLabel('最小化').click();
    await page.locator('.desktop-panel').waitFor({ state: 'hidden' });
    await page.locator('.side-desktop-live').waitFor();
    await page.screenshot({ path: shot('desktop-minimized') });
    await page.waitForTimeout(500);
    assert.equal(sockets.length, 1);
    assert.equal(sockets[0].isClosed(), false, 'still connected while minimized');

    await openDesktop(page);
    await page.locator('.desktop-panel').waitFor();
    assert.equal(await page.locator('.desktop-panel .term-note').count(), 0, 'no reconnecting');
    assert.equal(await page.locator('.desktop-control').getAttribute('aria-pressed'), 'true', 'control is kept');
    const box = await page.locator('.desktop-screen canvas').boundingBox();
    assert.ok(box && box.width > 300, 'scaled again to the panel');
    assert.equal(await page.locator('.side-desktop-live').count(), 0);
    assert.equal(sockets.length, 1, 'the same connection');

    // Close disconnects; opening again is a new connection.
    await page.getByLabel('断开并关闭').click();
    await page.locator('.desktop-panel').waitFor({ state: 'detached' });
    await waitUntil(() => sockets[0].isClosed());
    await openDesktop(page);
    await connected(page);
    assert.equal(sockets.length, 2);
  } finally {
    await page.close();
  }
});

test('the clipboard goes both ways: through the sheet, and Ctrl+V / Ctrl+C on a keyboard', { skip: skipClip }, async () => {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, permissions: ['clipboard-read', 'clipboard-write'] });
  const page = await ctx.newPage();
  const env = { ...process.env, DISPLAY: `:${open.display}` };
  const remoteClip = () => {
    try {
      return execFileSync(xclip, ['-o', '-selection', 'clipboard'], { env, encoding: 'utf8', timeout: 3000, stdio: ['ignore', 'pipe', 'ignore'] });
    } catch {
      return '';
    }
  };
  // xclip stays to serve what it was given, as a desktop app that copied would.
  const copyOnDesktop = (text) => {
    const child = spawn(xclip, ['-selection', 'clipboard'], { env, stdio: ['pipe', 'ignore', 'ignore'] });
    children.push(child);
    child.stdin.end(text);
  };
  const localClip = () => page.evaluate(() => navigator.clipboard.readText());
  try {
    await openUi(page, daemonOpen);
    await openDesktop(page);
    await connected(page);
    // Control, the state the desktop opens in, carries the clipboard; view only does not.
    await page.locator('.desktop-clip-btn').waitFor();
    await page.locator('.desktop-control').click();
    assert.equal(await page.locator('.desktop-clip-btn').count(), 0, 'no clipboard while view only');
    await page.locator('.desktop-control').click();

    // The desktop's copy: a dot on the button, then the sheet copies it here.
    copyOnDesktop('来自桌面 copied');
    await page.locator('.desktop-clip-btn.fresh').waitFor({ timeout: 10_000 });
    await page.locator('.desktop-clip-btn').click();
    await page.locator('.desktop-clip-text', { hasText: '来自桌面 copied' }).waitFor();
    assert.equal(await page.locator('.desktop-clip-btn.fresh').count(), 0, 'seen once the sheet is open');
    await page.getByRole('button', { name: '复制到本机' }).click();
    await page.getByText('已复制到本机').waitFor();
    assert.equal(await localClip(), '来自桌面 copied');

    // This device's text, pasted into the sheet, becomes the desktop's clipboard.
    await page.getByLabel('把本机的文字粘贴到这里').fill('发到桌面 sent');
    await page.getByRole('button', { name: '发到桌面剪贴板' }).click();
    await waitUntil(() => remoteClip() === '发到桌面 sent');
    for (const dark of [false, true]) {
      await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light' });
      await page.screenshot({ path: shot(`desktop-clipboard-${dark ? 'dark' : 'light'}`) });
    }
    await page.emulateMedia({ colorScheme: 'light' });
    await page.keyboard.press('Escape');
    await page.locator('.desktop-clip').waitFor({ state: 'detached' });

    // With a keyboard, Ctrl+V pastes this device's clipboard on the desktop...
    await page.evaluate(() => navigator.clipboard.writeText('粘贴 pasted'));
    await page.locator('.desktop-screen canvas').click();
    await page.keyboard.press('Control+V');
    await waitUntil(() => remoteClip() === '粘贴 pasted');
    // ...and what the desktop copies right after Ctrl+C comes here by itself.
    await page.keyboard.press('Control+C');
    copyOnDesktop('复制 copied back');
    await waitUntil(async () => (await localClip()) === '复制 copied back');
  } finally {
    await ctx.close();
  }
});

test('on plain http (no secure context) copying here still works, from the button and right after Ctrl+C', { skip: skipClip }, async () => {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, permissions: ['clipboard-read', 'clipboard-write'] });
  // As on a VPN address: no navigator.clipboard for the page, only the copy command after a gesture.
  await ctx.addInitScript(() => Object.defineProperty(window, 'isSecureContext', { get: () => false }));
  const page = await ctx.newPage();
  const env = { ...process.env, DISPLAY: `:${open.display}` };
  const copyOnDesktop = (text) => {
    const child = spawn(xclip, ['-selection', 'clipboard'], { env, stdio: ['pipe', 'ignore', 'ignore'] });
    children.push(child);
    child.stdin.end(text);
  };
  const localClip = () => page.evaluate(() => navigator.clipboard.readText());
  try {
    await openUi(page, daemonOpen);
    await openDesktop(page);
    await connected(page);
    await page.locator('.desktop-screen canvas').click();
    await page.keyboard.press('Control+C');
    copyOnDesktop('http copied back');
    await waitUntil(async () => (await localClip()) === 'http copied back');
    copyOnDesktop('http from button');
    await page.locator('.desktop-clip-btn.fresh').waitFor({ timeout: 10_000 });
    await page.locator('.desktop-clip-btn').click();
    await page.getByRole('button', { name: '复制到本机' }).click();
    await page.getByText('已复制到本机').waitFor();
    assert.equal(await localClip(), 'http from button');
  } finally {
    await ctx.close();
  }
});

test('the VNC password is asked, asked again when wrong, then kept on the device', { skip }, async () => {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  try {
    await openThread(page, daemonLocked, '有密码的桌面');
    await openDesktop(page);
    const field = page.locator('.dialog input');
    await field.waitFor();
    assert.equal(await page.locator('.dialog').count(), 1);
    assert.equal(await field.getAttribute('type'), 'password');
    await field.fill('wrong123');
    await page.getByRole('button', { name: '连接', exact: true }).click();
    await page.getByText('密码不对，请重新输入').waitFor({ timeout: 15_000 });
    await page.locator('.dialog input').fill('secret12');
    await page.getByRole('button', { name: '连接', exact: true }).click();
    await connected(page);

    await page.getByLabel('关闭').last().click();
    await page.reload();
    await openDesktop(page);
    await connected(page);
    assert.equal(await page.locator('.dialog').count(), 0, 'not asked again');
  } finally {
    await ctx.close();
  }
});

test('a VNC server that turns this address away says so, and the panel still closes', { skip }, async () => {
  // Five connections that leave before signing in: TigerVNC then refuses the address for a while.
  for (let i = 0; i < 6; i++) {
    await new Promise((resolve) => {
      const s = connect(blocked.port, '127.0.0.1');
      s.once('data', () => s.destroy());
      s.on('close', resolve);
      s.on('error', resolve);
    });
  }
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  try {
    await openUi(page, daemonBlocked);
    await openDesktop(page);
    await page.getByText('认证失败次数过多').waitFor({ timeout: 15_000 });
    await page.waitForTimeout(400); // past the panel's fade-in
    await page.screenshot({ path: shot('desktop-blocked') });
    assert.equal(await page.locator('.dialog').count(), 0, 'not taken for a wrong password');
    await page.locator('.desktop-panel').getByLabel('关闭').click();
    await page.locator('.desktop-panel').waitFor({ state: 'detached' });
  } finally {
    await page.close();
  }
});

test('on a phone the desktop opens from the sidebar ready to use: no Control switch, two floating pills', { skip }, async () => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await ctx.newPage();
  try {
    await openThread(page, daemonPhone ?? daemonOpen, '手机桌面');
    await page.locator('.menu-btn').click();
    await page.locator('.side-more').click();
    await page.getByRole('menuitem', { name: '桌面', exact: true }).waitFor();
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.waitForTimeout(500); // past the drawer sliding in
    await page.screenshot({ path: shot('desktop-phone-sidebar-dark') });
    await page.emulateMedia({ colorScheme: 'light' });
    await page.getByRole('menuitem', { name: '桌面', exact: true }).click();
    await connected(page);
    assert.equal(await page.locator('.desktop-control').count(), 0, 'no Control switch on a phone');
    assert.equal(await page.locator('.desktop-input').count(), 0, 'no typing bar');
    await page.locator('.desktop-pad').waitFor();
    assert.equal(await page.locator('.desktop-cursor').count(), 0, 'the finger is the pointer: none drawn');
    // The machine as the hub names it, never the desktop's own user@host.
    assert.equal((await page.locator('.desktop-machine-name').innerText()).trim(), '这台电脑');
    assert.equal(await page.locator('.desktop-panel').getByText(/@/).count(), 0, 'no user@host in the panel');
    assert.equal(await page.locator('.desktop-rotate').count(), 0, 'no turn-your-phone hint');
    for (const name of ['后退', '前进', '键盘', '复制桌面上选中的内容', '粘贴本机剪贴板']) await page.getByRole('button', { name, exact: true }).waitFor();
    await page.waitForTimeout(300); // past the panel fading in
    for (const dark of [false, true]) {
      await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light' });
      await page.screenshot({ path: shot(`desktop-phone-${dark ? 'dark' : 'light'}`) });
    }
    await page.emulateMedia({ colorScheme: 'light' });

    // The rest is in the menu: touchpad, view only, the clipboard sheet, minimize.
    await page.locator('.desktop-more').click();
    await page.getByRole('menuitemcheckbox', { name: /触控板/ }).waitFor();
    await page.waitForTimeout(300);
    for (const dark of [false, true]) {
      await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light' });
      await page.screenshot({ path: shot(`desktop-phone-menu-${dark ? 'dark' : 'light'}`) });
    }
    await page.emulateMedia({ colorScheme: 'light' });
    await page.getByRole('menuitem', { name: '剪贴板' }).click();
    await page.getByLabel('把本机的文字粘贴到这里').waitFor();
    assert.equal(await page.locator('.desktop-clip-hint').count(), 0, 'no keyboard shortcuts to mention on a phone');
    await page.getByLabel('把本机的文字粘贴到这里').fill('发到桌面的文字');
    await page.waitForTimeout(300);
    for (const dark of [false, true]) {
      await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light' });
      await page.screenshot({ path: shot(`desktop-phone-clipboard-${dark ? 'dark' : 'light'}`) });
    }
    await page.emulateMedia({ colorScheme: 'light' });
    await page.keyboard.press('Escape');

    // Minimized from the menu, still connected.
    await page.locator('.desktop-more').click();
    await page.getByRole('menuitem', { name: '最小化' }).click();
    await page.locator('.desktop-panel').waitFor({ state: 'hidden' });
  } finally {
    await ctx.close();
  }
});

/** The buttons xev saw pressed since `from`, with where. */
function pressed(from = 0) {
  return [...xevOut.slice(from).matchAll(/ButtonPress event[\s\S]*?root:\((\d+),(\d+)\)[\s\S]*?button (\d+)/g)].map((m) => ({ x: +m[1], y: +m[2], button: +m[3] }));
}

/** Fingers on the screen through CDP: each step is the list of points touching then. */
async function touches(cdp, steps, gap = 16) {
  for (const [i, points] of steps.entries()) {
    const type = i === 0 ? 'touchStart' : points.length ? 'touchMove' : 'touchEnd';
    await cdp.send('Input.dispatchTouchEvent', { type, touchPoints: points.map(([x, y], id) => ({ x, y, id })) });
    await new Promise((r) => setTimeout(r, gap));
  }
}

/** A phone opens filled to the height; a pinch in takes the picture back to the whole of it. */
async function fitWhole(page, cdp) {
  const box = await page.locator('.desktop-viewport').boundingBox();
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  const steps = [];
  for (let d = 0; d <= 6; d++) steps.push([[cx - 140 + d * 19, cy], [cx + 140 - d * 19, cy]]);
  await touches(cdp, [...steps, []], 30);
  await page.waitForFunction((w) => Math.abs(document.querySelector('.desktop-content').getBoundingClientRect().width - w) < 3, box.width);
}

/** A menu item behind the phone's ⋯ button. */
async function chooseFromMenu(page, name) {
  await page.locator('.desktop-more').click();
  await page.getByRole('menuitemcheckbox', { name }).click();
}

/** Where a point of the 1024x640 desktop is on the screen. */
async function onScreen(page, x, y) {
  const box = await page.locator('.desktop-screen canvas').boundingBox();
  return [box.x + ((x + 0.5) / 1024) * box.width, box.y + ((y + 0.5) / 640) * box.height];
}

const near = (p, x, y) => Math.abs(p.x - x) <= 2 && Math.abs(p.y - y) <= 2;

const contentWidth = (page) => page.locator('.desktop-content').evaluate((el) => el.getBoundingClientRect().width);

test('on a phone, view only: pinch and double tap zoom the picture, and nothing reaches the desktop', { skip: skipTouch }, async () => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  try {
    await openUi(page, daemonTouchy);
    await openDesktop(page);
    await connected(page);
    await chooseFromMenu(page, /只看/);
    await page.locator('.desktop-pad').waitFor({ state: 'detached' });
    await page.locator('.desktop-touch-mode').waitFor();
    const from = xevOut.length;
    const box = await page.locator('.desktop-viewport').boundingBox();
    const cx = box.x + box.width / 2;
    const cy = box.y + box.height / 2;

    // A phone opens filled to the height, not the whole picture squeezed into the width.
    const opened = await contentWidth(page);
    assert.ok(opened > box.width * 2, `opened filled to the height: ${opened}`);
    // A double tap goes back to the whole picture ...
    await page.touchscreen.tap(cx, cy);
    await page.waitForTimeout(80);
    await page.touchscreen.tap(cx, cy);
    await page.waitForFunction((w) => Math.abs(document.querySelector('.desktop-content').getBoundingClientRect().width - w) < 2, box.width);
    const fit = box.width;
    // ... and another fills the height again.
    await page.touchscreen.tap(cx, cy);
    await page.waitForTimeout(80);
    await page.touchscreen.tap(cx, cy);
    await page.waitForFunction((w) => document.querySelector('.desktop-content').getBoundingClientRect().width > w * 1.5, fit);

    // From the whole picture a pinch zooms in on the spot.
    await page.touchscreen.tap(cx, cy);
    await page.waitForTimeout(80);
    await page.touchscreen.tap(cx, cy);
    await page.waitForFunction((w) => Math.abs(document.querySelector('.desktop-content').getBoundingClientRect().width - w) < 2, fit);
    const spread = [];
    for (let d = 40; d <= 160; d += 20) spread.push([[cx - d, cy], [cx + d, cy]]);
    await touches(cdp, [...spread, []]);
    const zoomed = await contentWidth(page);
    assert.ok(zoomed > fit * 2.5, `pinched out: ${fit} -> ${zoomed}`);
    await page.waitForTimeout(300);
    await page.screenshot({ path: shot('desktop-phone-zoomed') });

    // A double tap goes back to the whole picture, and another fills the height again.
    await page.touchscreen.tap(cx, cy);
    await page.waitForTimeout(80);
    await page.touchscreen.tap(cx, cy);
    await page.waitForFunction((w) => Math.abs(document.querySelector('.desktop-content').getBoundingClientRect().width - w) < 2, fit);
    await page.touchscreen.tap(cx, cy);
    await page.waitForTimeout(80);
    await page.touchscreen.tap(cx, cy);
    await page.waitForFunction((w) => document.querySelector('.desktop-content').getBoundingClientRect().width > w * 1.5, fit);
    await page.waitForTimeout(300);
    assert.deepEqual(pressed(from), [], 'view only: no clicks on the desktop');
  } finally {
    await ctx.close();
  }
});

test('on a phone, the touchpad from the menu: move, tap, two-finger tap, scroll, long-press drag', { skip: skipTouch }, async () => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  try {
    await openUi(page, daemonTouchy);
    await openDesktop(page);
    await connected(page);
    await chooseFromMenu(page, /触控板/);
    await page.locator('.desktop-pad.touchpad').waitFor();

    const pad = await page.locator('.desktop-pad').boundingBox();
    // Anywhere on the pad: the pointer starts in the middle of the desktop (1024x640).
    const x = pad.x + 60;
    const y = pad.y + pad.height - 120;
    let from = xevOut.length;
    await touches(cdp, [[[x, y]], []]);
    await waitUntil(() => pressed(from).length === 1);
    assert.deepEqual(pressed(from), [{ x: 512, y: 320, button: 1 }], 'a tap clicks where the pointer is, not where the finger is');

    // A slow slide moves the pointer right; the next tap clicks there.
    const slide = [];
    for (let i = 0; i <= 10; i++) slide.push([[x + i * 6, y]]);
    await touches(cdp, [...slide, []], 30);
    from = xevOut.length;
    await touches(cdp, [[[x, y]], []]);
    await waitUntil(() => pressed(from).length === 1);
    const moved = pressed(from)[0];
    assert.ok(moved.x > 512 && moved.y === 320, `moved right: ${JSON.stringify(moved)}`);

    // Two fingers tapped: a right click.
    from = xevOut.length;
    await touches(cdp, [[[x, y], [x + 50, y]], []]);
    await waitUntil(() => pressed(from).length === 1);
    assert.equal(pressed(from)[0].button, 3);

    // Two fingers moving up together: the wheel, scrolling down (button 5).
    from = xevOut.length;
    const scroll = [];
    for (let i = 0; i <= 12; i++) scroll.push([[x, y - i * 12], [x + 50, y - i * 12]]);
    await touches(cdp, [...scroll, []]);
    await waitUntil(() => pressed(from).some((p) => p.button === 5));
    assert.ok(pressed(from).every((p) => p.button === 5));

    // A long press, then a slide: the button stays down through the move.
    from = xevOut.length;
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
    await page.waitForTimeout(600);
    await waitUntil(() => pressed(from).length === 1);
    for (let i = 1; i <= 5; i++) {
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x + i * 8, y }] });
      await page.waitForTimeout(30);
    }
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await waitUntil(() => /ButtonRelease event/.test(xevOut.slice(from)));
    assert.equal(pressed(from).length, 1, 'one press for the whole drag');

    for (const dark of [false, true]) {
      await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light' });
      await page.screenshot({ path: shot(`desktop-phone-touchpad-${dark ? 'dark' : 'light'}`) });
    }
  } finally {
    await ctx.close();
  }
});

test('on a phone the finger is the pointer: tap clicks there, long press right-clicks, long press and drag holds, two fingers scroll, one finger pans the picture', { skip: skipTouch }, async () => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  const sent = (from) => /Button(Press|Release) event/.test(xevOut.slice(from));
  try {
    await openUi(page, daemonTouchy);
    await openDesktop(page);
    await connected(page);
    await page.locator('.desktop-pad').waitFor();
    // A phone opens filled to the height; back to the whole picture so every spot below is on screen.
    await fitWhole(page, cdp);

    // A tap: a left click on the spot under the finger.
    let from = xevOut.length;
    const [x, y] = await onScreen(page, 200, 150);
    await touches(cdp, [[[x, y]], []]);
    await waitUntil(() => pressed(from).length === 1);
    let p = pressed(from)[0];
    assert.ok(p.button === 1 && near(p, 200, 150), `left click at 200,150: ${JSON.stringify(p)}`);

    // Two taps in a row land on the same spot: a double click.
    await page.waitForTimeout(400);
    from = xevOut.length;
    const [dx, dy] = await onScreen(page, 600, 400);
    await touches(cdp, [[[dx, dy]], []]);
    await touches(cdp, [[[dx + 3, dy + 2]], []]);
    await waitUntil(() => pressed(from).length === 2);
    const [first, second] = pressed(from);
    assert.ok(first.button === 1 && second.button === 1 && near(first, 600, 400), `two left clicks at 600,400: ${JSON.stringify([first, second])}`);
    assert.deepEqual([second.x, second.y], [first.x, first.y], 'on the same spot');

    // A long press: a right click there.
    await page.waitForTimeout(400);
    from = xevOut.length;
    const [rx, ry] = await onScreen(page, 300, 300);
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: rx, y: ry }] });
    await page.waitForTimeout(650);
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await waitUntil(() => pressed(from).length === 1);
    p = pressed(from)[0];
    assert.ok(p.button === 3 && near(p, 300, 300), `right click at 300,300: ${JSON.stringify(p)}`);

    // A long press, then a drag: the left button held down from where it started, through the move.
    from = xevOut.length;
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
    await page.waitForTimeout(650);
    for (let i = 1; i <= 6; i++) {
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x + i * 8, y }] });
      await page.waitForTimeout(30);
    }
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await waitUntil(() => /ButtonRelease event/.test(xevOut.slice(from)));
    p = pressed(from);
    assert.equal(p.length, 1, 'one press for the whole drag');
    assert.ok(p[0].button === 1 && near(p[0], 200, 150), `held from the start: ${JSON.stringify(p[0])}`);

    // Two fingers moving up together: the wheel, scrolling down (button 5); down: up (button 4).
    from = xevOut.length;
    const scroll = [];
    for (let i = 0; i <= 12; i++) scroll.push([[x, y + 150 - i * 12], [x + 60, y + 150 - i * 12]]);
    await touches(cdp, [...scroll, []]);
    await waitUntil(() => pressed(from).some((q) => q.button === 5));
    assert.ok(pressed(from).every((q) => q.button === 5), `only wheel down: ${JSON.stringify(pressed(from))}`);
    from = xevOut.length;
    await touches(cdp, [...[...scroll].reverse(), []]);
    await waitUntil(() => pressed(from).some((q) => q.button === 4));
    assert.ok(pressed(from).every((q) => q.button === 4));

    // One finger dragged across the whole picture: nothing reaches the desktop.
    await page.waitForTimeout(300);
    from = xevOut.length;
    const drag = (x0, y0, ddx, ddy) => {
      const steps = [];
      for (let i = 0; i <= 10; i++) steps.push([[x0 + (ddx * i) / 10, y0 + (ddy * i) / 10]]);
      return touches(cdp, [...steps, []]);
    };
    await drag(x, y, 0, -120);
    await page.waitForTimeout(400);
    assert.equal(sent(from), false, 'a one-finger drag sends nothing');

    // Two fingers spread: the picture zooms here, and nothing reaches the desktop.
    const fit = await contentWidth(page);
    const box = await page.locator('.desktop-viewport').boundingBox();
    const cx = box.x + box.width / 2;
    const cy = box.y + box.height / 2;
    const spread = [];
    for (let d = 40; d <= 140; d += 20) spread.push([[cx - d, cy], [cx + d, cy]]);
    await touches(cdp, [...spread, []]);
    assert.ok((await contentWidth(page)) > fit * 2, 'pinched out');

    // Zoomed in, one finger moves the picture to look elsewhere, still sending nothing.
    const scrolled = () => page.locator('.desktop-viewport').evaluate((el) => ({ left: el.scrollLeft, top: el.scrollTop }));
    const before = await scrolled();
    await drag(cx, cy, 120, 160);
    const after = await scrolled();
    // A wide desktop zoomed on an upright phone has little room up and down: the sideways move is the clear one.
    assert.ok(before.left - after.left > 80 && after.top <= before.top, `the picture followed the finger: ${JSON.stringify([before, after])}`);
    await page.waitForTimeout(400);
    assert.equal(sent(from), false, 'pinching and panning send nothing');
  } finally {
    await ctx.close();
  }
});

/** The keys xev saw pressed since `from`: keysym name and modifier state. */
function keysPressed(from = 0) {
  return [...xevKeys.slice(from).matchAll(/KeyPress event[\s\S]*?state (0x[0-9a-f]+), keycode \d+ \(keysym 0x[0-9a-f]+, (\w+)\)/g)].map((m) => ({ key: m[2], state: parseInt(m[1], 16) }));
}

test('on a phone the special keys, from the machine menu, reach the desktop and stay out on this device', { skip: skipTouch }, async () => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await ctx.newPage();
  try {
    await openUi(page, daemonTouchy);
    await openDesktop(page);
    await connected(page);
    assert.equal(await page.getByRole('toolbar', { name: '特殊键' }).count(), 0, 'closed by default');
    await page.locator('.desktop-more').click();
    await page.getByRole('menuitemcheckbox', { name: '特殊键' }).click();
    await page.getByRole('toolbar', { name: '特殊键' }).waitFor();

    // Esc: one key.
    let from = xevKeys.length;
    await page.getByRole('button', { name: 'Esc', exact: true }).tap();
    await waitUntil(() => keysPressed(from).some((k) => k.key === 'Escape'));

    // Ctrl, then c from the phone's keyboard: Ctrl+C.
    from = xevKeys.length;
    await page.getByRole('button', { name: 'Ctrl', exact: true }).tap();
    await page.getByRole('button', { name: '键盘', exact: true }).tap();
    await page.waitForFunction(() => document.activeElement?.classList.contains('desktop-ime'));
    await page.keyboard.type('c');
    await waitUntil(() => keysPressed(from).some((k) => k.key === 'c'));
    const pressedKeys = keysPressed(from);
    assert.equal(pressedKeys[0].key, 'Control_L');
    assert.ok(pressedKeys.find((k) => k.key === 'c').state & 0x4, `c with Control held: ${JSON.stringify(pressedKeys)}`);
    assert.equal(await page.getByRole('button', { name: 'Ctrl', exact: true }).getAttribute('aria-pressed'), 'false', 'let go after one key');

    // Kept on the device: open again after a reload.
    await page.reload();
    await openDesktop(page);
    await connected(page);
    await page.getByRole('toolbar', { name: '特殊键' }).waitFor();
    await page.getByRole('button', { name: '收起特殊键' }).tap();
    await page.reload();
    await openDesktop(page);
    await connected(page);
    await page.waitForTimeout(300);
    assert.equal(await page.getByRole('toolbar', { name: '特殊键' }).count(), 0, 'closed stays closed');
  } finally {
    await ctx.close();
  }
});

test('on a phone the system keyboard types on the desktop, Chinese through the input method included', { skip: skipClip }, async () => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  try {
    await openUi(page, daemonPhone);
    await openDesktop(page);
    await connected(page);
    // The special keys come from the menu as a strip over the pills, which stay as they are.
    await page.locator('.desktop-more').click();
    await page.getByRole('menuitemcheckbox', { name: '特殊键' }).click();
    await page.getByRole('toolbar', { name: '特殊键' }).waitFor();
    const ctrl = page.getByRole('button', { name: 'Ctrl', exact: true });
    await ctrl.tap();
    assert.equal(await ctrl.getAttribute('aria-pressed'), 'true', 'Ctrl latched for the next key');
    await ctrl.tap();
    assert.equal(await ctrl.getAttribute('aria-pressed'), 'false');
    await page.getByRole('button', { name: '后退' }).waitFor();

    await page.getByRole('button', { name: '键盘', exact: true }).tap();
    await page.waitForFunction(() => document.activeElement?.classList.contains('desktop-ime'));
    // The keyboard up: its button puts it away; the same two pills stay, frosted over the picture.
    await page.getByRole('button', { name: '收起键盘' }).waitFor();
    await page.locator('.desktop-dock.over').waitFor();
    await page.getByRole('button', { name: '后退' }).waitFor();

    await page.keyboard.type('hello ');
    // Pinyin being composed is not sent; the characters it becomes are.
    await cdp.send('Input.imeSetComposition', { text: 'ni', selectionStart: 2, selectionEnd: 2 });
    await cdp.send('Input.imeSetComposition', { text: 'nihao', selectionStart: 5, selectionEnd: 5 });
    await cdp.send('Input.insertText', { text: '你好' });
    // Backspace rubs out on the desktop: a stray letter, then gone.
    await page.keyboard.type('x');
    await page.keyboard.press('Backspace');
    await page.keyboard.press('Enter');
    await waitForFile(phone.out);
    assert.equal(readFileSync(phone.out, 'utf8'), 'hello 你好\n');
    assert.equal(await page.locator('.desktop-ime').inputValue(), '_'.repeat(32), 'the field is back to its fill');

    // A phone keyboard shown: the app box shrinks to what is left and the pills ride above it, while the
    // picture stays where it was, neither shrunk nor moved.
    const picture = await page.locator('.desktop-screen canvas').boundingBox();
    await page.setViewportSize({ width: 390, height: 508 });
    await page.waitForTimeout(300);
    const dock = await page.locator('.desktop-dock').boundingBox();
    assert.ok(dock.y + dock.height <= 508 - 12, 'the pills stay in sight, above the keyboard');
    assert.deepEqual(await page.locator('.desktop-screen canvas').boundingBox(), picture, 'the picture does not move');
    for (const dark of [false, true]) {
      await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light' });
      await page.screenshot({ path: shot(`desktop-phone-keyboard-keys-${dark ? 'dark' : 'light'}`) });
    }
    await page.getByRole('button', { name: '收起特殊键' }).tap();
    await page.getByRole('toolbar', { name: '特殊键' }).waitFor({ state: 'detached' });
    for (const dark of [false, true]) {
      await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light' });
      await page.screenshot({ path: shot(`desktop-phone-keyboard-${dark ? 'dark' : 'light'}`) });
    }
    await page.getByRole('button', { name: '收起键盘' }).tap();
    await page.getByRole('button', { name: '键盘', exact: true }).waitFor();
    assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('desktop-ime')), false);
  } finally {
    await ctx.close();
  }
});

test('on a phone, copy brings the desktop selection here and paste sends this clipboard there, one tap each', { skip: skipClip }, async () => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, permissions: ['clipboard-read', 'clipboard-write'] });
  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  const env = { ...process.env, DISPLAY: `:${phone.display}` };
  const remoteClip = () => {
    try {
      return execFileSync(xclip, ['-o', '-selection', 'clipboard'], { env, encoding: 'utf8', timeout: 3000, stdio: ['ignore', 'pipe', 'ignore'] });
    } catch {
      return '';
    }
  };
  try {
    await openUi(page, daemonPhone);
    await openDesktop(page);
    await connected(page);
    // A phone opens filled to the height; the word to select sits at the terminal's top left.
    await fitWhole(page, cdp);

    // Paste: this device's clipboard becomes the desktop's, and Ctrl+V follows.
    await page.evaluate(() => navigator.clipboard.writeText('手机粘贴 pasted'));
    await page.getByRole('button', { name: '粘贴本机剪贴板' }).tap();
    await page.getByRole('button', { name: '已粘贴到桌面' }).waitFor();
    await waitUntil(() => remoteClip() === '手机粘贴 pasted');

    // Copy: a double tap selects the word in the terminal; the copy button brings it here.
    await page.evaluate(() => navigator.clipboard.writeText('stale'));
    const [x, y] = await onScreen(page, 16, 8);
    await touches(cdp, [[[x, y]], []]);
    await touches(cdp, [[[x, y]], []]);
    await page.locator('.desktop-copy-btn.fresh').waitFor({ timeout: 10_000 });
    await page.getByRole('button', { name: '复制桌面上选中的内容' }).tap();
    await page.getByRole('button', { name: '已复制到本机' }).waitFor();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'copyme');
    assert.equal(await page.locator('.desktop-copy-btn.fresh').count(), 0, 'seen once copied');

    // Nothing new on the desktop: copy says so, in a line over the buttons, and copies nothing.
    await page.waitForTimeout(1500);
    await page.getByRole('button', { name: '复制桌面上选中的内容' }).tap();
    await page.getByRole('status').filter({ hasText: '桌面上没有新复制的内容' }).waitFor();
    for (const dark of [false, true]) {
      await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light' });
      await page.screenshot({ path: shot(`desktop-phone-toast-${dark ? 'dark' : 'light'}`) });
    }
  } finally {
    await ctx.close();
  }
});

test('on a phone over plain http, paste falls back to the clipboard sheet', { skip: skipClip }, async () => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  await ctx.addInitScript(() => Object.defineProperty(window, 'isSecureContext', { get: () => false }));
  const page = await ctx.newPage();
  const env = { ...process.env, DISPLAY: `:${phone.display}` };
  try {
    await openUi(page, daemonPhone);
    await openDesktop(page);
    await connected(page);
    await page.getByRole('button', { name: '粘贴本机剪贴板' }).tap();
    await page.getByLabel('把本机的文字粘贴到这里').fill('手动粘贴 by hand');
    await page.locator('.desktop-clip').getByRole('button', { name: '粘贴到桌面' }).click();
    await page.locator('.desktop-clip').getByText('已粘贴到桌面').waitFor();
    await waitUntil(() => {
      try {
        return execFileSync(xclip, ['-o', '-selection', 'clipboard'], { env, encoding: 'utf8', timeout: 3000, stdio: ['ignore', 'pipe', 'ignore'] }) === '手动粘贴 by hand';
      } catch {
        return false;
      }
    });
  } finally {
    await ctx.close();
  }
});

function shot(name) {
  return join(process.env.CALLI_SHOTS ?? join(tmpdir(), 'calli-shots'), `${name}.png`);
}

async function waitUntil(check, timeout = 10_000) {
  const start = Date.now();
  while (!(await check())) {
    if (Date.now() - start > timeout) throw new Error('timed out waiting');
    await new Promise((r) => setTimeout(r, 100));
  }
}

async function waitForFile(path, timeout = 10_000) {
  const start = Date.now();
  while (!existsSync(path) || !readFileSync(path, 'utf8')) {
    if (Date.now() - start > timeout) throw new Error(`${path} was not written`);
    await new Promise((r) => setTimeout(r, 100));
  }
}
