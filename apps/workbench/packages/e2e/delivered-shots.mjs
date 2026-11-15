// Run `npm run build` from the repository root before capturing.
// Screenshots of a delivered message — the one line a member's report folds into — in the states
// the team's protocol produces: for review, needing the person, failed. Folded and opened, desktop
// and phone, light and dark, and the task row waiting in a member's own thread. The demo workspace
// carries the example conversation; the daemon below only serves the built page.
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { startDaemon, launchBrowser } from './harness.mjs';

const outDir = join(process.cwd(), 'shots', 'delivered');
mkdirSync(outDir, { recursive: true });

const daemon = await startDaemon();
const browser = await launchBrowser();

try {
  // The page's own look, not a hand on the DOM: a context whose system asks for dark, and for the
  // Nord palette a saved appearance read before the first paint.
  const context = async (width, height, look = 'light', mobile = false) => {
    const made = await browser.newContext({
      viewport: { width, height },
      colorScheme: look === 'light' ? 'light' : 'dark',
      ...(mobile ? { isMobile: true, hasTouch: true } : {}),
    });
    if (look === 'nord-dark') {
      await made.addInitScript(
        (palette) => localStorage.setItem('calli.appearance', JSON.stringify({ mode: 'dark', palette })),
        'nord',
      );
    }
    return made;
  };
  const open = async (made) => {
    const page = await made.newPage();
    page.setDefaultTimeout(15_000);
    await page.goto(daemon.base + '/');
    return page;
  };

  // What each row says, read from the page itself: who, the dot's state, the word, the one-line sum.
  const rowsOf = (page) => page.evaluate(() => [...document.querySelectorAll('.row.delivered')].map((row) => {
    const sum = row.querySelector('.delivered-sum');
    const style = sum ? getComputedStyle(sum) : null;
    return {
      from: row.querySelector('.delivered-from')?.textContent ?? '',
      dot: (row.querySelector('.delivered-dot')?.className ?? '').replace('delivered-dot', '').trim(),
      word: row.querySelector('.delivered-status')?.textContent ?? null,
      sum: sum?.textContent ?? '',
      oneLine: Boolean(style) && style.whiteSpace === 'nowrap' && style.overflow === 'hidden' && style.textOverflow === 'ellipsis',
      height: row.querySelector('.delivered-line')?.getBoundingClientRect().height ?? 0,
    };
  }));
  const by = (rows, from) => rows.find((row) => row.from === from);
  const expect = (rows, name, dot, word) => {
    const row = by(rows, name);
    if (!row) throw new Error(`no delivered row for ${name}: ${JSON.stringify(rows)}`);
    if (row.dot !== dot || row.word !== word) throw new Error(`${name} shows ${row.dot}/${row.word}, wanted ${dot}/${word}`);
    if (!row.oneLine) throw new Error(`${name}'s first sentence runs past one line`);
    return row;
  };
  const shot = (page, name) => page.screenshot({ path: join(outDir, `${name}.png`) });
  const settle = (page) => page.waitForTimeout(250);

  // The wrapup conversation, opened and given the whole width: the margin card comes with it.
  const openWrapup = async (made) => {
    const page = await open(made);
    await page.locator('.thread-row', { hasText: '上线前收尾' }).click();
    await page.keyboard.press('Control+Shift+S');
    await page.locator('.app.sidebar-hidden').waitFor();
    await page.locator('.delivered-line').nth(2).waitFor();
    return page;
  };

  // The desktop reading of the three rows and what they fold out to, in both looks.
  for (const look of ['light', 'dark', 'nord-dark']) {
    const made = await context(1440, 900, look);
    const page = await openWrapup(made);
    if (look === 'light') {
      const rows = await rowsOf(page);
      if (rows.length !== 3) throw new Error(`expected three deliveries, found ${rows.length}`);
      expect(rows, '接口审查', 'review', '待验收');
      expect(rows, '深色核对', 'need', '需要你');
      expect(rows, '统计补齐', 'failed', '失败');
      if (!by(rows, '接口审查').sum.includes('核对完了')) throw new Error(`the review row says "${by(rows, '接口审查').sum}"`);
      if (!by(rows, '统计补齐').sum.includes('手机 390 尺寸')) throw new Error(`the failed row says "${by(rows, '统计补齐').sum}"`);
    }
    await settle(page);
    await shot(page, `rows-1440-${look}`);

    // Opened in place: the message as Markdown under the line, and a way into the sender's thread.
    // The pointer steps off the row after the click, so the picture shows it without the hover wash.
    await page.locator('.delivered-line', { hasText: '接口审查' }).click();
    await page.locator('.delivered-body').waitFor();
    await page.mouse.move(0, 0);
    if (look === 'light') {
      const opened = await page.evaluate(() => ({
        padding: getComputedStyle(document.querySelector('.delivered-body')).paddingLeft,
        says: document.querySelector('.delivered-body .markdown')?.textContent ?? '',
        foot: document.querySelector('.delivered-foot .text-btn')?.textContent ?? null,
      }));
      if (opened.padding !== '15px') throw new Error(`the body sits at ${opened.padding}, not under the name`);
      if (!opened.says.includes('按 seq 排序')) throw new Error('the opened body lost the message');
      if (opened.says.includes('[进度]')) throw new Error('the protocol line is shown in the body');
      if (!opened.foot?.includes('打开成员线程')) throw new Error('the way into the member thread is missing');
    }
    await settle(page);
    await shot(page, `expanded-1440-${look}`);

    // The way in goes there: the member's own thread, where the lead's task waits as the same kind
    // of line — a task this time, with no way further in.
    await page.locator('.delivered-foot .text-btn').click();
    await page.locator('.delivered-line', { hasText: '任务' }).waitFor();
    if (look === 'light') {
      const taskRows = await rowsOf(page);
      expect(taskRows, '主智能体', '', '任务');
      if (await page.locator('.delivered-foot').count()) throw new Error('a task from the lead offers a member thread');
    }
    await settle(page);
    await shot(page, `member-task-1440-${look}`);
    await page.close();
    await made.close();
  }

  // The failed member's own thread: the task it was given is all it ever got. The sidebar nests
  // members under their lead; the group opens to reach them. The row says what the member is
  // doing, so the name it goes by sits in its tooltip.
  {
    const made = await context(1440, 900, 'light');
    const page = await open(made);
    const sidebarLead = page.locator('.sidebar .team-group', { has: page.locator('.thread-title', { hasText: '上线前收尾' }) });
    await sidebarLead.waitFor();
    if (!(await sidebarLead.locator('.handover-member').count())) await sidebarLead.locator('.handover-toggle').click();
    await sidebarLead.locator('.handover-member[title="统计补齐"]').click();
    await page.locator('.delivered-sum', { hasText: '把统计口径补齐' }).waitFor();
    await settle(page);
    await shot(page, 'member-failed-task-1440-light');
    await page.close();
    await made.close();
  }

  // The phone: the same lines, taller for a finger, with opening the row a different tap from the
  // button below it.
  for (const look of ['light', 'dark']) {
    const made = await context(390, 844, look, true);
    const page = await open(made);
    // On a phone the thread list waits behind the menu; the wrapup conversation is what we read.
    await page.locator('.menu-btn').click();
    await page.locator('.thread-row', { hasText: '上线前收尾' }).click();
    await page.locator('.delivered-line').nth(2).waitFor();
    const rows = await rowsOf(page);
    expect(rows, '接口审查', 'review', '待验收');
    expect(rows, '深色核对', 'need', '需要你');
    expect(rows, '统计补齐', 'failed', '失败');
    for (const row of rows) if (row.height < 44) throw new Error(`${row.from}'s line is ${row.height}px tall, a finger needs 44`);
    await settle(page);
    await shot(page, `rows-390-${look}`);
    if (look === 'light') {
      await page.locator('.delivered-line', { hasText: '深色核对' }).click();
      await page.locator('.delivered-body').waitFor();
      await page.mouse.move(0, 0);
      const taps = await page.evaluate(() => {
        const line = [...document.querySelectorAll('.delivered-line')].find((el) => el.textContent.includes('深色核对'));
        const row = line?.getBoundingClientRect();
        const foot = document.querySelector('.delivered-foot .text-btn')?.getBoundingClientRect();
        return { row: row?.height ?? 0, foot: foot?.height ?? 0, apart: foot ? foot.top - (row?.bottom ?? 0) : -1 };
      });
      if (taps.foot < 44) throw new Error(`the open-member button is ${taps.foot}px tall, a finger needs 44`);
      if (taps.apart < 0) throw new Error('opening the row and opening the member thread are one tap area');
      await settle(page);
      await shot(page, 'expanded-390-light');
    }
    await page.close();
    await made.close();
  }

  console.log(`shots written to ${outDir}`);
} finally {
  await browser.close();
  await daemon.stop();
}
