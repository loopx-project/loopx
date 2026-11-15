// Screenshots of the auto-handover notice in its new one-line form, on the real UI.
// Run: node packages/e2e/shots.mjs  (this file; not part of the test runner)
import { mkdtempSync, writeFileSync, mkdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { api, startDaemon, openUi, launchBrowser, repoRoot } from './harness.mjs';

const shots = join(repoRoot, 'e2e', 'shots', 'auto-handover');
let dir, daemon, browser;
const quota = (agent, gemini = 10, claude = 20) => writeFileSync(join(dir, `${agent}.json`), JSON.stringify({ 'Gemini Models': gemini, 'Claude Models': claude }));
const request = async (path, method = 'GET', body) => {
  const res = await api(daemon, path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  if (!res.ok) throw new Error(`${path} answered ${res.status}: ${await res.text()}`);
  return res.json();
};
const wait = async fn => {
  const end = Date.now() + 20000;
  for (;;) { const value = await fn(); if (value) return value; if (Date.now() > end) throw new Error('timed out'); await new Promise(r => setTimeout(r, 60)); }
};
try {
  dir = mkdtempSync(join(tmpdir(), 'calli-notice-shots-')); mkdirSync(shots, { recursive: true });
  for (const id of ['agy', 'agy2', 'agy3']) quota(id);
  daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: [
    ...['agy', 'agy2', 'agy3'].map((id, i) => ({ id, adapter: 'agy', label: `Agent ${i + 1}`, command: ['fake-agy'], env: { FAKE_HANDOVER_QUOTA: join(dir, `${id}.json`) } })),
  ] }) });
  browser = await launchBrowser();
  const source = await request('/api/threads', 'POST', { agent: 'agy', model: 'gemini-pro', cwd: daemon.workdir, title: '主动交接' });
  await request('/api/agents/agy/quota');
  await request('/api/handover-settings', 'PUT', { enabled: true, threshold: 90, orders: { agy: [{ agent: 'agy2', model: 'gemini-pro' }] } });
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await context.newPage();
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const mobile = await phone.newPage();
  await openUi(page, daemon);
  await page.locator('.sidebar .thread-item').filter({ has: page.locator('.thread-title', { hasText: '主动交接' }) }).click();
  await request(`/api/threads/${source.id}/turns`, 'POST', { text: 'quota-near' });
  await page.locator('.auto-notice').filter({ hasText: '即将交接给' }).waitFor({ timeout: 15000 });
  const shoot = async (target, name, theme) => {
    await target.emulateMedia({ colorScheme: theme === 'dark' ? 'dark' : 'light' });
    await target.evaluate(t => document.documentElement.setAttribute('data-theme', t), theme);
    await target.locator('.auto-notice').scrollIntoViewIfNeeded();
    await target.screenshot({ path: join(shots, `notice-${name}.png`), animations: 'disabled' });
  };
  await shoot(page, 'desktop-light', 'light');
  await shoot(page, 'desktop-dark', 'dark');
  // The phone needs the thread selected in its own context, then the same notice.
  await openUi(mobile, daemon);
  await mobile.locator('.menu-btn').click();
  await mobile.locator('.sidebar .thread-item').filter({ has: mobile.locator('.thread-title', { hasText: '主动交接' }) }).click();
  await mobile.waitForFunction(() => getComputedStyle(document.querySelector('.sidebar')).visibility === 'hidden' && Math.abs(document.querySelector('.main').getBoundingClientRect().x) < 1, null, { timeout: 5000 });
  await mobile.locator('.auto-notice').filter({ hasText: /即将交接给|正在准备交接/ }).waitFor({ timeout: 15000 });
  await shoot(mobile, 'phone-light', 'light');
  await shoot(mobile, 'phone-dark', 'dark');
  console.log('notice shots written to', shots);
  await context.close(); await phone.close();
} catch (err) {
  console.error('FAILED:', err.message);
  if (daemon) console.error('--- daemon log tail ---\n' + daemon.log().split('\n').slice(-30).join('\n'));
  process.exitCode = 1;
} finally {
  await browser?.close();
  await daemon?.stop();
  if (dir) rmSync(dir, { recursive: true, force: true });
}
