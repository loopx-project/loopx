// A「需要你」row in the matters drawer names either a session or one of her asks (`run:<id>`); the
// ask must land in the tray above the composer, where it is answered, never on a new-thread page
// with `thread not found`. The plan and the proactive runs are scripted routes, as the tray shots
// script them, because a scripted run is the only way to hold one in `asking`.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { api, startDaemon, launchBrowser, openUi } from '../harness.mjs';

const request = async (daemon, path, method = 'GET', body) => {
  const res = await api(daemon, path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  if (!res.ok) throw new Error(`${path} answered ${res.status}: ${await res.text()}`);
  return res.status === 204 ? null : res.json();
};
const wait = async fn => {
  const end = Date.now() + 60_000;
  for (;;) { const value = await fn(); if (value) return value; if (Date.now() > end) throw new Error('timed out'); await new Promise(r => setTimeout(r, 200)); }
};

test('a needs-you row leads to the ask or the session it names, never a thread-not-found page', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    await request(daemon, '/api/personal/settings', 'PUT', { agent: 'pi', directory: daemon.workdir });
    const { threadId } = await request(daemon, '/api/personal/say', 'POST', { text: '今天要开周会，最紧急的是什么？' });
    await wait(async () => (await request(daemon, `/api/threads/${threadId}`)).status === 'idle');

    const at = minutes => new Date(Date.now() - minutes * 60_000).toISOString();
    // One ask still asking, one already answered into a real session, one the plan still lists but
    // the daemon no longer knows: every way the drawer's row can be stale, led somewhere real.
    const runs = [
      { id: 'a1', kind: 'advance', threadId: 'gone-session', matterId: '', status: 'asking', reason: 'user', startedAt: at(2), question: { text: '确认要不要我把这段替换进周报文档', options: ['替换', '先不换'] } },
      { id: 'a2', kind: 'advance', threadId, matterId: '', status: 'answered', reason: 'user', startedAt: at(30), line: '周报模板已按新格式整理好' },
    ];
    const needsYou = [
      { id: 'run:a1', title: '确认要不要我把这段替换进周报文档', line: '等你选替换或先不换', at: at(2) },
      { id: 'run:a2', title: '周报模板已按新格式整理好', line: '已办好，去看会话', at: at(30) },
      { id: 'run:a3', title: '一条已经不在的问话', line: '列表里还留着', at: at(40) },
      { id: threadId, title: '一个等答复的会话', line: '停在等你确认', at: at(15) },
    ];

    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.route(/\/api\/personal\/proactive(\?.*)?$/, route => {
      const url = new URL(route.request().url());
      if (url.searchParams.has('summary')) return route.fulfill({ json: { settings: { mode: 'auto', enabled: true, repos: [] }, counts: {}, asking: { '': 1 } } });
      return route.fulfill({ json: { settings: { mode: 'auto', enabled: true, repos: [] }, runs } });
    });
    await page.route(/\/api\/personal\/plan\?range=\w+$/, async route => {
      const range = new URL(route.request().url()).searchParams.get('range');
      return route.fulfill({ json: { range, from: at(60), to: at(0), meetings: { state: 'off', items: [] }, urgent: [], due: [], goals: [], needsYou, running: [] } });
    });

    await openUi(page, daemon);
    const openMatters = async () => {
      // Off the matters address first, so the drawer always gets a fresh landing even when the
      // previous click left the address where it was.
      await page.evaluate(() => { localStorage.removeItem('calli.planRange'); location.hash = '#/desk'; location.hash = '#/desk/matters'; });
      await page.locator('.plan-view .plan-list').first().waitFor();
    };
    const notFound = () => page.getByText('thread not found').count();

    // An ask still asking: the drawer closes on the tray, the ask open with its answer controls.
    await openMatters();
    await page.locator('.plan-text', { hasText: '确认要不要我把这段替换进周报文档' }).click();
    await page.locator('.chat-ask[data-run="a1"].open').waitFor();
    assert.equal(await page.locator('.chat-drawer').count(), 0, 'the drawer gives way to the ask');
    await page.locator('.chat-ask[data-run="a1"]').getByRole('button', { name: '替换', exact: true }).waitFor();
    assert.equal(await notFound(), 0, 'an ask never opens a thread that does not exist');
    assert.equal(errors.length, 0);

    // An ask already answered: its session opens, as any other entry's thread does.
    await openMatters();
    await page.locator('.plan-text', { hasText: '周报模板已按新格式整理好' }).click();
    // The thread view shows the session's own words; the address keeps no thread hash (it is
    // replaced, not pushed), so the page's content is what says where the person landed.
    await page.getByText('今天要开周会，最紧急的是什么？').first().waitFor();
    assert.equal(await page.locator('.chat-home').count(), 0, 'the desk gave way to the session');
    assert.equal(await notFound(), 0);
    assert.equal(errors.length, 0);

    // An ask the daemon no longer knows: nowhere to go, so the desk stays and says nothing false.
    await openMatters();
    await page.locator('.plan-text', { hasText: '一条已经不在的问话' }).click();
    await page.waitForTimeout(300);
    assert.notEqual(await page.locator('.chat-home').count(), 0, 'the desk keeps the person where they were');
    assert.equal(await notFound(), 0);
    assert.equal(errors.length, 0);

    // A session's own row still opens the session.
    await openMatters();
    await page.locator('.plan-text', { hasText: '一个等答复的会话' }).click();
    await page.getByText('今天要开周会，最紧急的是什么？').first().waitFor();
    assert.equal(await page.locator('.chat-home').count(), 0);
    assert.equal(await notFound(), 0);
    assert.equal(errors.length, 0);

    await page.close();
  } finally {
    await browser.close();
    await daemon.stop();
  }
});
