// Search looks through what was said, not only titles: a hit names its thread, shows the words
// around the match, and opens the conversation at that turn.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon, browser, deep, shelved;
const post = async (path, body, method = 'POST') => {
  const response = await api(daemon, path, { method, body: JSON.stringify(body) });
  assert.ok(response.ok, await response.clone().text());
  return response.json();
};
const say = async (id, text) => {
  await post(`/api/threads/${id}/turns`, { text });
  for (let n = 0; n < 200; n++) {
    if ((await (await api(daemon, `/api/threads/${id}`)).json()).status !== 'running') return;
    await new Promise(r => setTimeout(r, 50));
  }
  throw new Error('thread did not finish');
};

before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
  deep = await post('/api/threads', { agent: 'claude', cwd: daemon.workdir, title: '长对话' });
  await say(deep.id, '先确认独角鲸迁徙路线的数据来源');
  for (let n = 1; n <= 8; n++) await say(deep.id, `第 ${n} 件别的事\n${'这一段只是为了把前面的内容顶出屏幕。\n'.repeat(6)}`);
  shelved = await post('/api/threads', { agent: 'claude', cwd: daemon.workdir, title: '放进归档的线程' });
  await say(shelved.id, '记一下：灯塔水母的养殖笔记');
});
after(async () => { await browser?.close(); await daemon?.stop(); });

for (const phone of [false, true]) for (const colorScheme of ['light', 'dark']) {
  test(`search finds words in conversations and opens the turn: ${phone ? 'phone' : 'desktop'} ${colorScheme}`, async () => {
    await post(`/api/threads/${shelved.id}`, { archived: true }, 'PATCH');
    const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: phone, hasTouch: phone, colorScheme });
    const page = await context.newPage();
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    const shots = '/tmp/calli-search-shots'; mkdirSync(shots, { recursive: true });
    const shot = name => page.screenshot({ path: `${shots}/${phone ? 'phone' : 'desktop'}-${colorScheme}-${name}.png` });
    try {
      await openUi(page, daemon);
      if (phone) await page.locator('.menu-btn').click();
      await page.getByRole('button', { name: '搜索线程', exact: true }).click();
      const box = page.getByRole('searchbox');
      const hits = page.getByRole('region', { name: '对话内容' });

      // One character is in every conversation; nothing is looked up for it.
      await box.fill('独');
      assert.equal(await hits.count(), 0);
      // No title has the words, the first turn of the long thread does.
      await box.fill('独角鲸 迁徙');
      const hit = hits.locator('.search-hit', { hasText: '我：' });
      await hit.waitFor();
      assert.equal(await hit.count(), 1);
      assert.match(await hit.innerText(), /长对话[\s\S]*我：[\s\S]*独角鲸迁徙路线/);
      assert.deepEqual(await hit.locator('mark').allInnerTexts(), ['独角鲸', '迁徙']);
      await page.getByText('没有标题或目录匹配的线程。').waitFor();
      await shot('hits');

      // Opening it shows that turn, though the thread opens at its end and the turn is far above.
      await hit.click();
      const asked = page.locator('.thread .row.user', { hasText: '独角鲸迁徙路线' });
      await asked.waitFor();
      await page.waitForFunction(() => document.querySelector('.thread .row.found'));
      const at = await asked.boundingBox();
      assert.ok(at.y >= 0 && at.y < page.viewportSize().height - 100, `the found turn is on screen (${at.y})`);
      await shot('opened');

      // Words nobody said find nothing; an archived thread is found, marked, and comes back when opened.
      if (phone) await page.locator('.menu-btn').click();
      await box.fill('从来没人说过的话');
      await hits.getByText('对话里没有找到。').waitFor();
      await box.fill('灯塔水母');
      await hits.locator('.search-hit', { hasText: '已归档' }).first().click();
      await page.getByText('已从归档恢复').waitFor();
      await page.locator('.thread .row.user', { hasText: '灯塔水母的养殖笔记' }).waitFor();
      assert.equal((await (await api(daemon, `/api/threads/${shelved.id}`)).json()).archivedAt, null);
      assert.deepEqual(errors, []);
    } finally { await context.close(); }
  });
}
