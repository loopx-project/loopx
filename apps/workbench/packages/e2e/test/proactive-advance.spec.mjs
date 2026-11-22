import { describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, launchBrowser, openUi } from '../harness.mjs';
import { arrangeAdvance, routes } from './proactive-fixture.mjs';

describe('legacy assistant workspace', { skip: 'LoopX Workbench replaces the legacy assistant and plans pages with the steward and Goals.' }, () => {

const openDesk = async (page, daemon) => {
  await openUi(page, daemon);
  // The desk is the drawer's own tab on the assistant's home; its address opens it directly.
  await page.evaluate(() => { location.hash = '#/desk/desk'; });
  await page.getByRole('button', { name: '按进度', exact: true }).click();
  await page.locator('.desk-lanes').waitFor();
};
const closeSpread = async page => {
  await page.keyboard.press('Escape');
  await page.locator('.desk-spread').waitFor({ state: 'detached' });
};
const screenshot = async (page, name) => {
  if (!process.env.LOOCHAT_PROACTIVE_SCREENSHOTS) return;
  mkdirSync(process.env.LOOCHAT_PROACTIVE_SCREENSHOTS, { recursive: true });
  await page.screenshot({ path: join(process.env.LOOCHAT_PROACTIVE_SCREENSHOTS, `${name}.png`), fullPage: true, animations: 'disabled' });
};

for (const width of [1440, 390]) for (const colorScheme of ['light', 'dark']) {
  const mode = colorScheme === 'dark' ? 'observe' : 'auto';
  test(`advance cards: ${width}px ${colorScheme}, ${mode}`, async () => {
    const daemon = await startDaemon();
    const browser = await launchBrowser();
    try {
      const data = await arrangeAdvance(daemon);
      data.mode = mode;
      const page = await browser.newPage({ viewport: { width, height: width === 390 ? 844 : 1000 }, isMobile: width === 390, hasTouch: width === 390, colorScheme, timezoneId: 'UTC' });
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      await routes(page, data);
      await openDesk(page, daemon);
      await page.getByRole('button', { name: '2 个问题等你回答', exact: true }).waitFor();
      assert.deepEqual(data.requests.searches, [''], 'the conversation reads its decision cards; desk covers add only the summary');
      for (const todo of data.todos.slice(0, 2)) await page.locator(`#desk-pile-${todo.id}`).getByText('等你回答', { exact: true }).waitFor();
      await screenshot(page, `desk-${width}-${colorScheme}`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'no horizontal page overflow');

      for (const [index, runId] of ['question', 'done', 'waiting', 'permission'].entries()) {
        await page.locator(`#desk-pile-${data.todos[index].id}`).click();
        const card = page.locator(`.proactive-card[data-run="${runId}"]`);
        await card.waitFor();
        await screenshot(page, `${runId}-${width}-${colorScheme}`);
        if (runId === 'question') {
          await card.getByText('先部署到预发还是生产？', { exact: true }).waitFor();
          await card.getByRole('button', { name: '其他回答', exact: true }).click();
          const sizes = await card.evaluate(card => {
            const input = card.querySelector('input');
            const send = card.querySelector('form button');
            const option = card.querySelector('.desk-card-actions button');
            return { heights: [input, send, option].map(node => node.offsetHeight), fonts: [input, send, option].map(node => getComputedStyle(node).fontSize), sameRow: input.offsetTop === send.offsetTop };
          });
          assert.deepEqual(sizes.heights, [40, 40, 40]);
          assert.deepEqual(sizes.fonts, ['12px', '12px', '12px']);
          assert.equal(sizes.sameRow, true);
          assert.equal(await card.getByRole('button', { name: '发送回答', exact: true }).isEnabled(), false);
          data.failNext.answer = '对话正在运行';
          await card.getByRole('button', { name: '先到预发', exact: true }).click();
          await card.getByRole('alert').getByText('对话正在运行').waitFor();
          await card.getByRole('button', { name: '先到预发', exact: true }).click();
          await card.getByText('已回答', { exact: true }).waitFor();
          assert.deepEqual(data.requests.answers.at(-1), { answer: '先到预发' });
          assert.equal(await card.getByRole('alert').count(), 0);
        } else if (runId === 'done') {
          await card.getByText('看起来做完了', { exact: true }).waitFor();
          await card.getByText('导出修复已通过全部测试', { exact: true }).waitFor();
          await card.getByRole('button', { name: '关掉事项', exact: true }).click();
          await page.locator('.desk-spread').getByText('解决了', { exact: true }).first().waitFor();
          assert.equal(data.runs[1].status, 'closed');
        } else if (runId === 'waiting') {
          await card.getByText('在等：持续集成检查 · 12:30 再看', { exact: true }).waitFor();
          await card.getByRole('button', { name: '不等了', exact: true }).click();
          await card.waitFor({ state: 'detached' });
        } else {
          await card.getByText('/workspace/project', { exact: true }).waitFor();
          await card.getByRole('button', { name: '允许', exact: true }).click();
          await card.getByText('已认可', { exact: true }).waitFor();
        }
        await closeSpread(page);
      }
      await page.locator('.desk-attention').waitFor({ state: 'hidden' });
      assert.deepEqual(data.requests.actions.filter(action => !action.endsWith('/answer')), ['done/close-matter', 'waiting/dismiss', 'permission/allow-repo']);
      assert.deepEqual(errors, []);
    } finally { await browser.close(); await daemon.stop(); }
  });
}

test('attention entry supports free answers, declining completion and declining sandbox permission', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    const data = await arrangeAdvance(daemon);
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await routes(page, data);
    await openDesk(page, daemon);
    const attention = page.getByRole('button', { name: '2 个问题等你回答', exact: true });
    assert.equal(await attention.evaluate(button => button.tabIndex), 0);
    await attention.focus();
    assert.equal(await attention.evaluate(button => button === document.activeElement), true);
    await page.keyboard.press('Enter');
    const list = page.locator('.proactive-list');
    await list.locator('li').first().waitFor();
    assert.equal(await list.locator('li').count(), 2, 'only matters waiting for the person');
    const question = list.locator(`[data-matter="${data.todos[0].id}"]`);
    // With options offered, writing one's own answer is one tap away.
    assert.equal(await question.getByRole('textbox').count(), 0);
    await question.getByRole('button', { name: '其他回答', exact: true }).click();
    await question.getByRole('textbox', { name: '其他回答', exact: true }).fill('  先补灰度验证  ');
    data.failNext.answer = '暂时无法发送';
    await question.getByRole('button', { name: '发送回答', exact: true }).click();
    await question.getByRole('alert').waitFor();
    assert.equal(await question.getByRole('textbox').inputValue(), '  先补灰度验证  ', 'failed send preserves the draft');
    await question.getByRole('button', { name: '发送回答', exact: true }).click();
    await question.waitFor({ state: 'detached' });
    assert.deepEqual(data.requests.answers.at(-1), { answer: '先补灰度验证' });
    await list.getByRole('button', { name: '还没完', exact: true }).click();
    await page.getByText('暂时没有需要你处理的卡片。', { exact: true }).waitFor();
    await page.getByRole('button', { name: '按进度', exact: true }).click();
    await page.locator(`#desk-pile-${data.todos[3].id}`).click();
    const permission = page.locator('.proactive-card[data-run="permission"]');
    await permission.getByRole('button', { name: '不用', exact: true }).click();
    await permission.waitFor({ state: 'detached' });
    assert.equal(data.runs[1].status, 'discarded');
    assert.equal(data.runs[3].status, 'discarded');
  } finally { await browser.close(); await daemon.stop(); }
});

test('attention total sums cards and reveals the next question on the same matter in observe mode', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    const data = await arrangeAdvance(daemon);
    data.mode = 'observe';
    data.runs.push({ ...data.runs[0], id: 'question-2', threadId: 'advance-thread-2', turnId: 'turn-2', startedAt: new Date(Date.now() + 1000).toISOString(), question: { text: '选择哪个发布窗口？', options: ['明早发布', '今晚发布'] } });
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await routes(page, data);
    await openDesk(page, daemon);
    await page.getByRole('button', { name: '3 个问题等你回答', exact: true }).click();
    const list = page.locator('.proactive-list');
    await list.getByText('选择哪个发布窗口？', { exact: true }).waitFor();
    assert.equal(await list.locator('li').count(), 2, 'three cards belong to two matters');
    await screenshot(page, 'multiple-asking-390-observe');
    const question = list.locator(`[data-matter="${data.todos[0].id}"]`);
    await question.getByRole('button', { name: '其他回答', exact: true }).click();
    await question.getByRole('textbox', { name: '其他回答', exact: true }).fill('这份草稿只属于发布时间');
    await question.getByRole('button', { name: '明早发布', exact: true }).click();
    await question.getByText('先部署到预发还是生产？', { exact: true }).waitFor();
    await page.getByRole('button', { name: '2 个问题等你回答', exact: true }).waitFor();
    assert.equal(await question.getByRole('textbox').count(), 0, 'a different question starts folded, never with the previous draft');
    assert.deepEqual(data.requests.answers.at(-1), { answer: '明早发布' });
    await question.getByRole('button', { name: '先到预发', exact: true }).click();
    await question.waitFor({ state: 'detached' });
    await page.getByRole('button', { name: '1 个问题等你回答', exact: true }).waitFor();
  } finally { await browser.close(); await daemon.stop(); }
});

for (const [locale, labels] of [
  ['en', { retry: 'Try again', leave: 'Leave for now', keep: 'Keep waiting', stop: 'Stop waiting' }],
  ['ja', { retry: '再試行する', leave: 'いったん保留', keep: '待ち続ける', stop: '待機をやめる' }],
]) test(`system choices display ${locale} but send the original protocol answers`, async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    const data = await arrangeAdvance(daemon);
    Object.assign(data.runs[0], { reason: 'transient', question: { text: '模型服务连续出错：429', options: ['再试一次', '先放着'] } });
    Object.assign(data.runs[1], { reason: 'user', question: { text: '请原样选择再试一次', options: ['再试一次'] } });
    Object.assign(data.runs[2], { status: 'asking', question: { text: '等了 6 次还在等检查结果', options: ['继续等', '不等了'] } });
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await routes(page, data);
    await openDesk(page, daemon);
    await page.evaluate(locale => localStorage.setItem('calli.locale', locale), locale);
    await page.reload();
    await page.getByRole('button', { name: locale === 'en' ? '3 questions waiting for you' : '3 件の質問が回答待ち', exact: true }).click();
    const list = page.locator('.proactive-list');
    const transient = list.locator(`[data-matter="${data.todos[0].id}"]`);
    const external = list.locator(`[data-matter="${data.todos[2].id}"]`);
    await transient.getByRole('button', { name: labels.leave, exact: true }).waitFor();
    await external.getByRole('button', { name: labels.keep, exact: true }).waitFor();
    await list.locator(`[data-matter="${data.todos[1].id}"]`).getByRole('button', { name: '再试一次', exact: true }).waitFor();
    await transient.getByRole('button', { name: labels.retry, exact: true }).click();
    await transient.waitFor({ state: 'detached' });
    assert.deepEqual(data.requests.answers.at(-1), { answer: '再试一次' });
    await external.getByRole('button', { name: labels.stop, exact: true }).click();
    await external.waitFor({ state: 'detached' });
    assert.deepEqual(data.requests.answers.at(-1), { answer: '不等了' });
    await page.getByRole('button', { name: locale === 'en' ? '1 question waiting for you' : '1 件の質問が回答待ち', exact: true }).waitFor();
  } finally { await browser.close(); await daemon.stop(); }
});

for (const width of [1440, 390]) for (const colorScheme of ['light', 'dark']) test(`a recent session with no matter asks from its own row and opens the session: ${width}px ${colorScheme}`, async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    const data = await arrangeAdvance(daemon);
    data.runs.push({ id: 'session', matterId: '', threadId: 'session-thread', threadTitle: '修复白屏', kind: 'advance', turnId: 'turn-s', startedAt: new Date().toISOString(), status: 'asking', reason: 'user', question: { text: '白屏已修好，要我合入 master 吗？', options: [] } });
    const page = await browser.newPage({ viewport: { width, height: width === 390 ? 844 : 1000 }, isMobile: width === 390, hasTouch: width === 390, colorScheme, timezoneId: 'UTC' });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await routes(page, data);
    await openDesk(page, daemon);
    await page.getByRole('button', { name: '3 个问题等你回答', exact: true }).click();
    const row = page.locator('.proactive-list li[data-matter="thread:session-thread"]');
    await row.getByRole('button', { name: '修复白屏', exact: true }).waitFor();
    await row.getByText('白屏已修好，要我合入 master 吗？', { exact: true }).waitFor();
    await screenshot(page, `session-${width}-${colorScheme}`);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'no horizontal page overflow');
    await row.getByRole('textbox').fill('合入吧');
    await row.getByRole('button', { name: '发送回答', exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('.proactive-list li[data-matter="thread:session-thread"] input'));
    assert.deepEqual(data.requests.answers, [{ answer: '合入吧' }]);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); await daemon.stop(); }
});

});
