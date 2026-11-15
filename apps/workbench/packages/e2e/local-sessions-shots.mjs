// Screenshots of local sessions mixed into the thread list (desktop 1440 and phone 390, light and
// dark): rows present, one importing, the "show more" row. Run after `npm run build`:
//   node packages/e2e/local-sessions-shots.mjs [outdir]
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, launchBrowser, openUi, api } from './harness.mjs';

const out = process.argv[2] ?? '/tmp/calli-local-sessions-shots';
mkdirSync(out, { recursive: true });

const workdir = mkdtempSync(join(tmpdir(), 'calli-local-shots-'));
const sessionsFile = join(workdir, 'sessions.json');
const now = Date.now();
const at = (minutesAgo) => new Date(now - minutesAgo * 60_000).toISOString();
// Two sessions inside the workdir (one a minute old, one an hour), one outside it, one undated.
writeFileSync(sessionsFile, JSON.stringify([
  { sessionId: 'ext-recent', cwd: workdir, title: '终端里改构建脚本', updatedAt: at(1) },
  { sessionId: 'ext-older', cwd: workdir, title: '上周排查的端口占用', updatedAt: at(62) },
  { sessionId: 'ext-nodate', cwd: workdir, title: '没有时间的旧会话', updatedAt: null },
  { sessionId: 'ext-outside', cwd: '/usr', title: '不在允许目录里的会话', updatedAt: at(2) },
]));

const daemon = await startDaemon(
  { FAKE_ACP_SESSIONS_FILE: sessionsFile },
  { workdir, agents: [
    { id: 'claude', adapter: 'claude', command: ['fake-claude'] },
    { id: 'pi', adapter: 'pi', command: ['fake-pi'] },
  ] },
);
// A thread of our own, so the list mixes both kinds under one project.
await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: workdir, title: '冒烟测试' }) });

// The first page read can land before the daemon's first scan finished; wait until the list
// answers with the sessions, so every screenshot sees the same state.
for (let i = 0; i < 100; i++) {
  const ready = await api(daemon, '/api/local-sessions');
  if (ready.ok && ((await ready.json()).sessions ?? []).length >= 3) break;
  await new Promise((r) => setTimeout(r, 300));
}

const browser = await launchBrowser();
try {
  for (const [name, viewport, scheme] of [
    ['desktop-light', { width: 1440, height: 900 }, 'light'],
    ['desktop-dark', { width: 1440, height: 900 }, 'dark'],
    ['phone-light', { width: 390, height: 844 }, 'light'],
    ['phone-dark', { width: 390, height: 844 }, 'dark'],
  ]) {
    const page = await browser.newPage({ viewport, colorScheme: scheme });
    page.on('console', (m) => { if (m.type() === 'error') console.log('[console]', m.text().slice(0, 300)); });
    page.on('response', (r) => { if (r.url().includes('local-sessions')) console.log('[net]', r.status()); });
    await openUi(page, daemon);
    console.log('ui open', name);
    // The sidebar's own list, with the machine's sessions mixed in by time.
    await page.locator('.thread-row.external').first().waitFor({ state: 'attached', timeout: 20_000 });
    if (viewport.width < 800) {
      // A phone keeps the list as a page of its own behind the menu button; give the connect
      // transition a moment so the click lands on the settled page.
      await page.waitForTimeout(600);
      await page.locator('.menu-btn').click();
      await page.locator('.drawer-open').waitFor({ timeout: 5000 });
      await page.waitForTimeout(500);
    }
    await page.screenshot({ path: join(out, `list-${name}.png`) });

    if (name === 'desktop-light') {
      // Clicking an external row imports it; the row says so while that runs, and the thread opens.
      await page.locator('.thread-row.external').first().locator('.thread-item').click();
      await page.locator('.thread-row.external').first().waitFor({ timeout: 20_000 });
      await page.screenshot({ path: join(out, `imported-${name}.png`) });
    }
    await page.close();
  }
  console.log(`shots in ${out}`);
  console.log(`daemon ${daemon.pid} (${daemon.base}), token ${daemon.token}`);
} finally {
  await browser.close();
  await daemon.stop();
}
