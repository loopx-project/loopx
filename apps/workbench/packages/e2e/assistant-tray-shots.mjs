// Screenshots of the assistant home with several asks folded into one tray above the composer, the
// tray opened on one ask, and the matters drawer's hours / today / week / month switch (desktop 1440
// and phone 390, light and dark). The asks and the plan are scripted routes. Run after `npm run build`:
//   node packages/e2e/assistant-tray-shots.mjs [outdir]
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { api, startDaemon, openUi, launchBrowser, repoRoot } from './harness.mjs';

const out = process.argv[2] ?? join(repoRoot, 'e2e', 'shots', 'assistant-tray');
mkdirSync(out, { recursive: true });
const request = async (daemon, path, method = 'GET', body) => {
  const res = await api(daemon, path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  if (!res.ok) throw new Error(`${path} answered ${res.status}: ${await res.text()}`);
  return res.status === 204 ? null : res.json();
};
const wait = async fn => {
  const end = Date.now() + 60_000;
  for (;;) { const value = await fn(); if (value) return value; if (Date.now() > end) throw new Error('timed out'); await new Promise(r => setTimeout(r, 200)); }
};

const daemon = await startDaemon();
const browser = await launchBrowser();
try {
  await request(daemon, '/api/personal/settings', 'PUT', { agent: 'pi', directory: daemon.workdir });
  const { threadId } = await request(daemon, '/api/personal/say', 'POST', { text: '今天要开周会，最紧急的是什么？' });
  await wait(async () => (await request(daemon, `/api/threads/${threadId}`)).status === 'idle');

  const now = Date.now();
  const at = min => new Date(now - min * 60_000).toISOString();
  const base = { kind: 'advance', threadId: 'advance-thread', status: 'asking', reason: 'user', matterId: '' };
  const runs = [
    { ...base, id: 'a1', startedAt: at(1), question: { text: '确认要不要我把这段替换进周报文档', options: ['替换', '先不换'] } },
    { ...base, id: 'a2', startedAt: at(2), question: { text: '试用 playground 后选出要做的增强，并决定钉住的目录存在 hub 上还是只存在当前浏览器。', options: [] } },
    { ...base, id: 'a3', startedAt: at(3), question: { text: '确认 sub session 的含义，以及同步等待要不要作为默认，然后再开始改', options: ['同步等待', '不等待'] } },
    { ...base, id: 'a4', startedAt: at(4), question: { text: 'devbox 还没更新，SSH 目前超时，请告诉我它恢复了，或者换一台机器。', options: [] } },
    { id: 'd1', kind: 'dispatch', status: 'asking', matterId: '', threadId: '', startedAt: at(5), agent: 'codex', cwd: daemon.workdir, title: '整理周会材料', task: '整理周会材料：汇总本周合入的 PR、未关的问题和下周计划，写成一页。' },
  ];
  const day = new Date(); day.setHours(0, 0, 0, 0);
  const clock = (h, m = 0) => new Date(day.getTime() + (h * 60 + m) * 60_000).toISOString();
  const todo = (id, text, extra = {}) => ({ id, text, dueAt: null, importance: null, horizon: null, parentId: null, stage: null, ...extra });
  const plans = {
    hour: { meetings: { state: 'ok', items: [{ id: 'm1', title: '团队周会', start: clock(14), end: clock(15), recurring: true }] }, urgent: [todo('t1', '回复周报替换', { importance: 'high', stage: 'answer' })], due: [], goals: [], needsYou: [{ id: 'th1', title: 'playground 增强', line: '等你选增强项', at: at(30) }], running: [] },
    day: { meetings: { state: 'ok', items: [{ id: 'm1', title: '团队周会', start: clock(14), end: clock(15), recurring: true }, { id: 'm2', title: '设计评审', start: clock(16, 30), end: clock(17), recurring: false }] }, urgent: [todo('t1', '回复周报替换', { importance: 'high', stage: 'answer' })], due: [todo('t2', '整理周会材料', { dueAt: clock(13, 30) }), todo('t3', '提交报销', { dueAt: clock(18) })], goals: [todo('g1', '助手能接管已有会话', { horizon: 'week' })], needsYou: [{ id: 'th1', title: 'playground 增强', line: '等你选增强项', at: at(30) }], running: [] },
    week: { meetings: { state: 'unavailable', error: 'lark-cli timed out', items: [] }, urgent: [], due: [1, 2, 3, 4, 5, 6, 7].map(i => todo(`w${i}`, `本周事项 ${i}`, { dueAt: clock(24 * i + 18) })), goals: [todo('g1', '助手能接管已有会话', { horizon: 'week' }), todo('g2', '时/天/周/月视图上线', { horizon: 'week' })], needsYou: [], running: [] },
    month: { meetings: { state: 'off', items: [] }, urgent: [], due: [], goals: [todo('g3', '个人助手能替我开周会', { horizon: 'month' })], needsYou: [], running: [] },
  };

  const shoot = async (page, name) => page.screenshot({ path: join(out, `${name}.png`), animations: 'disabled' });
  for (const [name, viewport, scheme] of [
    ['desktop-light', { width: 1440, height: 900 }, 'light'],
    ['desktop-dark', { width: 1440, height: 900 }, 'dark'],
    ['phone-light', { width: 390, height: 844 }, 'light'],
    ['phone-dark', { width: 390, height: 844 }, 'dark'],
  ]) {
    const page = await browser.newPage({ viewport, colorScheme: scheme });
    page.on('pageerror', e => console.log('[pageerror]', e.message));
    await page.route(/\/api\/personal\/proactive(\?.*)?$/, route => {
      const url = new URL(route.request().url());
      if (url.searchParams.has('summary')) return route.fulfill({ json: { settings: { mode: 'auto', enabled: true, repos: [] }, counts: {}, asking: { '': runs.length } } });
      return route.fulfill({ json: { settings: { mode: 'auto', enabled: true, repos: [] }, runs } });
    });
    await page.route(/\/api\/personal\/plan\?range=\w+$/, async route => {
      const range = new URL(route.request().url()).searchParams.get('range');
      await new Promise(r => setTimeout(r, 250));
      return route.fulfill({ json: { range, from: clock(0), to: clock(24), ...plans[range] } });
    });
    await openUi(page, daemon);
    await page.evaluate(() => { localStorage.removeItem('calli.planRange'); location.hash = '#/desk'; });
    await page.locator('.chat-tray').waitFor();
    await page.waitForTimeout(400);
    await shoot(page, `folded-${name}`);
    await page.locator('.chat-tray-head').click();
    await page.locator('.chat-ask[data-run="a1"] .chat-ask-row').click();
    await page.waitForTimeout(200);
    await shoot(page, `open-${name}`);
    await page.locator('.chat-entries').getByRole('button', { name: /事项/ }).click();
    await page.locator('.plan-view .plan-list').first().waitFor();
    await page.waitForTimeout(300);
    await shoot(page, `plan-day-${name}`);
    for (const range of ['hour', 'week', 'month']) {
      await page.locator('.plan-ranges button').nth(['hour', 'day', 'week', 'month'].indexOf(range)).click();
      await page.locator('.plan-skeleton').waitFor({ state: 'detached' });
      await page.waitForTimeout(150);
      await shoot(page, `plan-${range}-${name}`);
    }
    await page.close();
  }
  console.log(`shots in ${out}`);
} finally {
  await browser.close();
  await daemon.stop();
}
