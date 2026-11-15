// Screenshots of the inline pattern that replaced toasts: the menu-button attention dot, the
// archive-undo row under the header, the busy line by the thread title, and a mid-turn effort
// change that is accepted instead of refused. Run: node packages/e2e/pattern-d-shots.mjs
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { api, startDaemon, openUi, launchBrowser, repoRoot } from './harness.mjs';

const shots = join(repoRoot, 'e2e', 'shots', 'pattern-d');
let daemon, browser;
const request = async (path, method = 'GET', body) => {
  const res = await api(daemon, path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  if (!res.ok) throw new Error(`${path} answered ${res.status}: ${await res.text()}`);
  return res.json();
};
const wait = async (fn) => {
  const end = Date.now() + 20000;
  for (;;) { const value = await fn(); if (value) return value; if (Date.now() > end) throw new Error('timed out'); await new Promise((r) => setTimeout(r, 60)); }
};
const theme = (target, mode) => target.evaluate((m) => document.documentElement.setAttribute('data-theme', m), mode);
const open = async (page, title, exact = false) => {
  // A fork shares the source's title with a suffix, so an exact match picks the original.
  const match = exact ? new RegExp(`^${title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`) : title;
  await page.locator('.sidebar .thread-item').filter({ has: page.locator('.thread-title', { hasText: match }) }).first().click();
};
const shoot = async (target, locator, name) => {
  await target.locator(locator).first().screenshot({ path: join(shots, `${name}.png`), animations: 'disabled' });
  console.log(name);
};

try {
  mkdirSync(shots, { recursive: true });
  // The fork holds its copy open for a moment, so the busy line is on screen to catch; a turn's
  // first output is held too, so the effort can be changed while it runs.
  daemon = await startDaemon({ FAKE_PI_FORK_DELAY_MS: '2500', FAKE_PI_FIRST_OUTPUT_DELAY_MS: '9000' });
  browser = await launchBrowser();
  const desktop = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await desktop.newPage();
  const phone = await (await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })).newPage();
  await openUi(page, daemon);

  // A finished thread nobody has opened, beside the one on screen: the dot on the list button.
  // The button exists below 900px, where the sidebar folds away; on a wide screen there is no list
  // button to dot, the sidebar itself carries the marks.
  const quiet = await request('/api/threads', 'POST', { agent: 'pi', cwd: daemon.workdir, title: '后台线程' });
  await request(`/api/threads/${quiet.id}/turns`, 'POST', { text: '做完了' });
  const work = await request('/api/threads', 'POST', { agent: 'pi', cwd: daemon.workdir, title: '正在干的活' });
  await request(`/api/threads/${work.id}/turns`, 'POST', { text: '第一步' });
  const narrow = await (await browser.newContext({ viewport: { width: 880, height: 960 } })).newPage();
  await openUi(narrow, daemon);
  await narrow.locator('.menu-btn').click();
  await open(narrow, '正在干的活');
  await narrow.waitForFunction(() => getComputedStyle(document.querySelector('.sidebar')).visibility === 'hidden');
  await theme(narrow, 'light');
  await shoot(narrow, '.topbar', 'dot-desktop-light');
  await theme(narrow, 'dark');
  await shoot(narrow, '.topbar', 'dot-desktop-dark');
  await (await narrow.context()).close();

  await open(page, '正在干的活');

  // Archiving from the thread's row in the list: the row vanishes, the undo waits under the header.
  await page.locator('.sidebar .thread-row', { hasText: '正在干的活' }).click({ button: 'right' });
  await page.getByRole('menuitem', { name: '归档' }).click();
  await page.locator('.notice-row').waitFor({ timeout: 5000 });
  await theme(page, 'light');
  await shoot(page, '.main', 'undo-desktop-light');
  await theme(page, 'dark');
  await shoot(page, '.main', 'undo-desktop-dark');
  await page.getByRole('button', { name: '撤销' }).click();
  await page.locator('.notice-row').waitFor({ state: 'detached' });
  await open(page, '正在干的活');

  // A copy on its way: the busy line sits by the title until the new thread opens.
  await page.locator('.topbar-more').click();
  await page.getByRole('menuitem', { name: '分叉与交接', exact: true }).click();
  await page.getByRole('menuitem', { name: '分叉', exact: true }).click();
  await page.locator('.topbar-busy').waitFor({ timeout: 5000 });
  await theme(page, 'light');
  await shoot(page, '.topbar', 'busy-desktop-light');
  await theme(page, 'dark');
  await shoot(page, '.topbar', 'busy-desktop-dark');
  await page.waitForTimeout(3000);

  // The phone: the same three, on the way back from a thread.
  await openUi(phone, daemon);
  await phone.locator('.menu-btn').click();
  await open(phone, '正在干的活');
  await phone.waitForFunction(() => getComputedStyle(document.querySelector('.sidebar')).visibility === 'hidden');
  await theme(phone, 'light');
  await shoot(phone, '.topbar', 'dot-phone-light');
  await phone.locator('.menu-btn').click();
  await phone.locator('.sidebar .thread-row', { hasText: '正在干的活' }).click({ button: 'right' });
  await phone.getByRole('menuitem', { name: '归档' }).click();
  await phone.locator('.notice-row').waitFor({ timeout: 5000 });
  if (await phone.evaluate(() => document.documentElement.classList.contains('list-open'))) await phone.locator('.menu-btn').click();
  await shoot(phone, '.main', 'undo-phone-light');
  await phone.getByRole('button', { name: '撤销' }).click();
  await phone.locator('.menu-btn').click();
  await open(phone, '正在干的活', true);
  await phone.waitForFunction(() => getComputedStyle(document.querySelector('.sidebar')).visibility === 'hidden');

  // The original complaint: a turn running, the effort changed under it. No refusal, no pill.
  await theme(phone, 'dark');
  await phone.locator('.composer textarea').fill('wait-first');
  await phone.locator('.composer .send-btn:not(.stop)').click();
  try {
    await wait(async () => (await request(`/api/threads/${work.id}`)).status === 'running');
  } catch (err) {
    await phone.screenshot({ path: join(shots, 'FAIL-send.png') });
    throw new Error(`turn never ran; thread=${JSON.stringify(await request(`/api/threads/${work.id}`)).slice(0, 200)}`);
  }
  await phone.locator('.model-control-trigger').click();
  const slider = phone.getByRole('slider');
  await slider.focus();
  await slider.press('End');
  await phone.waitForTimeout(800);
  const after = await request(`/api/threads/${work.id}`);
  if (!after.effort) throw new Error('effort change did not land while the turn ran');
  if (await phone.locator('.toast').count()) throw new Error('a toast appeared');
  if (await phone.locator('.banner').count()) throw new Error('an error banner appeared');
  await shoot(phone, '.main', 'effort-mid-turn-phone-dark');
  await theme(phone, 'light');
  await shoot(phone, '.model-control-panel', 'effort-picker-phone-light');

  console.log('pattern-d shots written to', shots);
  await desktop.close(); await (await phone.context()).close();
} catch (err) {
  console.error('FAILED:', err.message);
  if (daemon) console.error('--- daemon log tail ---\n' + daemon.log().split('\n').slice(-30).join('\n'));
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  if (daemon) await daemon.stop();
}
