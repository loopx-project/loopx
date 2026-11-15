import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { deflateSync } from 'node:zlib';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon, browser;
const shots = process.env.CALLI_E2E_SHOTS_DIR;
const prefix = process.env.CALLI_E2E_SHOTS_PREFIX ?? 'writing';
before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
  if (shots) mkdirSync(shots, { recursive: true });
});
after(async () => { await browser?.close(); await daemon?.stop(); });

/** A small solid PNG, so the preview has a real picture to load. */
function png(width, height, [r, g, b]) {
  const crcTable = Array.from({ length: 256 }, (_, n) => {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    return c >>> 0;
  });
  const crc = (buf) => {
    let c = 0xffffffff;
    for (const byte of buf) c = crcTable[(c ^ byte) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  };
  const chunk = (type, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type), data]);
    const sum = Buffer.alloc(4); sum.writeUInt32BE(crc(body));
    return Buffer.concat([len, body, sum]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4);
  header[8] = 8; header[9] = 2;
  const rows = Buffer.alloc((width * 3 + 1) * height);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) rows.set([r, g, b], y * (width * 3 + 1) + 1 + x * 3);
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', header), chunk('IDAT', deflateSync(rows)), chunk('IEND', Buffer.alloc(0))]);
}

const NAME = '思考是人类的事情';
const BODY = `# ${NAME}

我好久没真正自己思考写东西了。现在很多时候，是想一句话，然后让 AI 帮我把一篇文章写出来。

今天写 PR 的时候不一样。我得真的去想，为什么要这样写。【待补：写到哪一句时发现想法变了】

〔补〕Zinsser 说过，写作就是在纸上思考。写不清楚，往往是因为还没想清楚。

## AI 可以做什么

观点得是我的，无论它多偏激。

\`\`\`mermaid
flowchart LR
  A[采访 · 提问] --> M((我的思考))
  B[资料 · 比喻] --> M
  M --> C[我的文章]
\`\`\`

![书桌](${NAME}/desk.png)
`;

/** A thread whose folder has a body under writing/, and a picture beside it. */
async function setup(title, cwd) {
  mkdirSync(join(cwd, 'writing', NAME), { recursive: true });
  const file = join(cwd, 'writing', `${NAME}.md`);
  writeFileSync(file, BODY);
  writeFileSync(join(cwd, 'writing', NAME, 'desk.png'), png(240, 120, [196, 160, 112]));
  const res = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd, model: 'fake/plain', title, permissionMode: 'full_access' }) });
  assert.equal(res.status, 201);
  return { thread: await res.json(), file };
}

const view = async (id) => (await api(daemon, `/api/threads/${id}`)).json();
const waitFor = async (check, what) => {
  const deadline = Date.now() + 15_000;
  for (;;) {
    const value = await check();
    if (value) return value;
    assert.ok(Date.now() < deadline, what);
    await new Promise((r) => setTimeout(r, 50));
  }
};
const waitIdle = (id) => waitFor(async () => { const v = await view(id); return v.status !== 'running' && v; }, 'turn finished');

const select = async (page, title, mobile) => {
  await openUi(page, daemon);
  if (mobile) await page.locator('.menu-btn').click();
  await page.locator('.thread-title', { hasText: title }).first().click();
};
const send = async (page, text) => {
  await page.locator('.composer textarea').first().fill(text);
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.composer textarea')?.value === '');
};
const fitsWidth = (page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
/** The stage chip's popover: opened, tapped through, closed again. */
const openStages = (page) => page.locator('.writing-chip').click();

test('writing mode on desktop: chip and stages, article preview, write and save, conflicts, end', { timeout: 240000 }, async () => {
  for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme });
    try {
      const page = await context.newPage();
      const shot = async (name) => shots && page.screenshot({ path: join(shots, `${prefix}-${name}-desktop-${colorScheme}.png`), animations: 'disabled' });
      const title = `写作 桌面 ${colorScheme}`;
      const cwd = join(daemon.workdir, `desk-${colorScheme}`);
      const { thread, file } = await setup(title, cwd);
      await select(page, title, false);

      // The add menu: writing mode sits with the modes, one click turns it on.
      await page.getByRole('button', { name: '添加与模式', exact: true }).click();
      const choice = page.getByRole('option', { name: /^写作模式/ });
      assert.equal(await choice.getAttribute('aria-checked'), 'false');
      await shot('menu');
      await choice.click();
      const bar = page.locator('.writing-bar');
      await bar.waitFor();
      await page.locator('.writing-chip', { hasText: '初稿' }).waitFor();
      assert.equal((await view(thread.id)).writing?.stage, 'draft');
      await page.locator('.composer textarea[placeholder="继续说，或者让 agent 补哪一段…"]').waitFor();

      // After a turn the daemon names the newest body under writing/.
      await send(page, '今天想写一篇关于思考的文章');
      await waitIdle(thread.id);
      await waitFor(async () => (await view(thread.id)).writing?.file, 'the body is named after the turn');

      // One chip; the stages live behind it, each with its word of advice, the current one checked.
      await openStages(page);
      const pop = page.getByRole('menu');
      await pop.waitFor();
      assert.equal(await pop.getByRole('menuitemradio', { name: /^初稿/ }).getAttribute('aria-checked'), 'true');
      await shot('stages');
      await pop.getByRole('menuitemradio', { name: /^二稿/ }).click();
      await page.locator('.writing-chip', { hasText: '二稿' }).waitFor();
      assert.equal((await view(thread.id)).writing.stage, 'second');

      // The body opens from the same popover: an article on a wide sheet, tree folded away.
      await openStages(page);
      await pop.getByRole('menuitem', { name: /^打开正文/ }).click();
      const panel = page.locator('.files-panel');
      await panel.locator('.files-markdown.article h1', { hasText: NAME }).waitFor();
      const panelBox = await panel.boundingBox();
      assert.ok(panelBox && panelBox.width >= 640, `the panel is ${panelBox?.width}px wide for the body`);
      assert.equal(await panel.locator('.files-tree').count(), 0, 'the tree starts folded for the body');
      // What the body takes, it leaves whole: the composer's bottom row keeps its full words.
      const composer = await page.evaluate(() => {
        const bar = [...document.querySelectorAll('.composer-bar')].at(-1);
        return [...(bar?.children ?? [])].filter((c) => c.scrollWidth > c.clientWidth + 1).length;
      });
      assert.equal(composer, 0, 'no button in the composer\u2019s bottom row is cut short');
      // The title says where the piece stands; the marks are colour, not punctuation.
      assert.match(await panel.locator('.article-meta').textContent(), /^二稿 · [\d,]+ 字$/);
      assert.equal(await panel.locator('.writing-todo').textContent(), '待补：写到哪一句时发现想法变了');
      assert.equal(await panel.locator('.writing-added').textContent(), 'Zinsser 说过，写作就是在纸上思考。写不清楚，往往是因为还没想清楚。');
      // The diagram takes the theme's own colours, and the relative picture loads.
      const diagram = panel.locator('.files-diagram svg');
      await diagram.waitFor({ timeout: 20_000 });
      const nodeFill = await diagram.locator('rect, path.node, polygon').first().evaluate((el) => getComputedStyle(el).fill);
      assert.notEqual(nodeFill, 'rgb(236, 236, 255)', 'mermaid is not left on its purple default');
      const picture = panel.locator('.files-inline-picture img');
      await picture.waitFor();
      await page.waitForFunction(() => (document.querySelector('.files-inline-picture img')?.naturalWidth ?? 0) === 240);
      await shot('preview');
      // A click on the diagram enlarges it.
      await panel.locator('.files-diagram').click();
      await page.locator('.lightbox').waitFor();
      await page.keyboard.press('Escape');
      await page.locator('.lightbox').waitFor({ state: 'detached' });

      // The writing habits note: offered once it exists, with a word about how it gets written.
      const habitsFile = join(daemon.notesDir, 'writing.md');
      rmSync(habitsFile, { force: true });
      await openStages(page);
      const habitsItem = pop.locator('.pop-item', { hasText: '写作习惯' });
      await habitsItem.getByText('说「复盘」后会生成').waitFor();
      await shot('habits-off');
      await page.keyboard.press('Escape');
      mkdirSync(daemon.notesDir, { recursive: true });
      writeFileSync(habitsFile, '# 写作习惯\n\n- 短句。× 冗长的从句。\n');
      await openStages(page);
      await pop.getByRole('menuitem', { name: /写作习惯/ }).click();
      await panel.locator('.files-markdown.article h1', { hasText: '写作习惯' }).waitFor();
      // Not this thread's draft: no stage over the title, only the count of what is written.
      assert.match(await panel.locator('.article-meta').textContent(), /^[\d,]+ 字$/);
      await shot('habits');
      // Back to the body for the writing that follows.
      await openStages(page);
      await pop.getByRole('menuitem', { name: /^打开正文/ }).click();
      await panel.locator('.files-markdown.article h1', { hasText: NAME }).waitFor();

      // Writing: the same sheet, turned over. The markers dim under the typing.
      await panel.getByRole('button', { name: '写', exact: true }).click();
      const editor = panel.getByRole('textbox', { name: '正文', exact: true });
      await editor.waitFor();
      assert.equal(await editor.inputValue(), BODY);
      assert.equal(await panel.locator('.files-num').count(), 0, 'no line numbers in the editor');
      assert.ok((await panel.locator('.writing-paper-layer .w-mk').count()) > 0, 'the marks are dimmed');
      // The layer behind the typing shares its every measure, so the caret lines up with what is seen.
      const metrics = await page.evaluate(() => {
        const read = (el) => { const c = getComputedStyle(el); return [c.font, c.lineHeight, c.paddingTop, c.paddingRight, c.paddingBottom, c.paddingLeft, c.letterSpacing].join('|'); };
        return { typing: read(document.querySelector('.writing-paper-input')), seen: read(document.querySelector('.writing-paper-layer')) };
      });
      assert.equal(metrics.typing, metrics.seen, 'the page behind the typing is set the same');
      await panel.getByText('已保存', { exact: true }).waitFor();
      const edited = BODY.replace('【待补：写到哪一句时发现想法变了】', '其实那一刻是写到「为什么不直接复用旧接口」的时候。');
      await editor.fill(edited);
      await panel.getByText('未保存', { exact: true }).waitFor();
      await panel.getByRole('button', { name: '写', exact: true }).click();
      assert.equal(await editor.inputValue(), edited, 'clicking write twice preserves the draft');
      await shot('edit');

      // Unsaved text outlives a reload.
      await page.waitForTimeout(600);
      await page.reload();
      await page.locator('.thread-title', { hasText: title }).first().click();
      await openStages(page);
      await page.getByRole('menu').getByRole('menuitem', { name: /^打开正文/ }).click();
      await panel.getByRole('textbox', { name: '正文', exact: true }).waitFor();
      assert.equal(await panel.getByRole('textbox', { name: '正文', exact: true }).inputValue(), edited, 'the draft came back after reload');
      await panel.getByRole('textbox', { name: '正文', exact: true }).focus();
      await page.keyboard.press('Control+s');
      await panel.getByText('未保存', { exact: true }).waitFor({ state: 'detached' });
      assert.equal(readFileSync(file, 'utf8'), edited);

      // The agent changes the body meanwhile: the save is refused, the text stays, overwrite wins.
      const mine = `${edited}\n我自己加的一段。\n`;
      await editor.fill(mine);
      writeFileSync(file, `${edited}\n〔补〕agent 补的一段。\n`);
      await panel.getByRole('button', { name: '保存', exact: true }).click();
      const conflict = panel.locator('.writing-conflict');
      await conflict.getByText('正文被改过。你写的还在下面，可以先复制。', { exact: true }).waitFor();
      assert.equal(await editor.inputValue(), mine, 'what was typed stays in the box');
      await shot('conflict');
      await conflict.getByRole('button', { name: '仍然覆盖', exact: true }).click();
      await conflict.waitFor({ state: 'detached' });
      assert.equal(readFileSync(file, 'utf8'), mine);

      // Again, and this time load the new version instead.
      await editor.fill(`${mine}再来一段。\n`);
      const theirs = `${mine}〔补〕又补了一段。\n`;
      writeFileSync(file, theirs);
      await page.keyboard.press('Control+s');
      await conflict.waitFor();
      await conflict.getByRole('button', { name: '载入新版本', exact: true }).click();
      await conflict.waitFor({ state: 'detached' });
      assert.equal(await editor.inputValue(), theirs);

      // Turning back to reading with unsaved text asks first.
      await editor.fill(`${theirs}没保存的。\n`);
      await panel.getByRole('button', { name: '读', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: '放弃没保存的修改？' });
      await dialog.waitFor();
      await dialog.getByRole('button', { name: '放弃修改', exact: true }).click();
      await editor.waitFor({ state: 'detached' });
      assert.equal(readFileSync(file, 'utf8'), theirs, 'discarding does not write');
      await panel.getByRole('button', { name: '写', exact: true }).waitFor();

      // A passage picked out of the body: the quick labels are there, one tap is the whole note,
      // and it carries the file it came from.
      // The preview has just come back from the writing page: its diagram settles last, so the
      // page is let still before a passage is picked out of it.
      await panel.locator('.files-diagram svg').waitFor({ timeout: 20_000 });
      // The pick is made near the passage's first words: dead centre can land on a mark of
      // punctuation, and a double click there selects the break instead of the word.
      await panel.locator('.files-markdown.article p', { hasText: '观点得是我的' }).dblclick({ position: { x: 30, y: 16 } });
      const selection = page.locator('.selection-menu');
      await selection.waitFor();
      assert.ok((await selection.locator('.selection-tag').count()) > 0, 'the writing body offers its quick labels');
      await shot('select');
      await selection.locator('.selection-tag', { hasText: '删' }).click();
      const strip = page.locator('.composer .note-strip');
      await strip.waitFor();
      const stripText = await strip.textContent();
      assert.ok(stripText?.includes(`writing/${NAME}.md`), 'the note carries the file it came from');
      assert.ok(stripText?.includes('删'), 'the note carries the label as its comment');
      // Sent, the note goes ahead of the message with the file's own line, and the agent reads it.
      await send(page, '按注释改');
      await waitIdle(thread.id);
      await page.getByText(`文件：writing/${NAME}.md`).first().waitFor();

      // End: the chip goes, the body stays.
      await openStages(page);
      await page.getByRole('menu').getByRole('menuitem', { name: '结束写作' }).click();
      await page.locator('.writing-bar').waitFor({ state: 'detached' });
      assert.equal((await view(thread.id)).writing, null);
      assert.equal(readFileSync(file, 'utf8'), theirs);
      assert.ok(await fitsWidth(page));
    } finally { await context.close(); }
  }
});

test('the open body leaves the composer its whole bottom row', { timeout: 120000 }, async () => {
  // A body takes most of the window, but never the composer's bottom line: the collaboration
  // button and the model's name keep their whole words at every desktop width.
  for (const vw of [1280, 1440, 1920]) {
    const context = await browser.newContext({ viewport: { width: vw, height: 900 } });
    try {
      const page = await context.newPage();
      const shot = async (name) => shots && page.screenshot({ path: join(shots, `${prefix}-${name}-desktop-light.png`), animations: 'disabled' });
      const title = `写作 宽度 ${vw}`;
      const { thread } = await setup(title, join(daemon.workdir, `width-${vw}`));
      await api(daemon, `/api/threads/${thread.id}/writing`, { method: 'PUT', body: JSON.stringify({ stage: 'second' }) });
      await select(page, title, false);
      await openStages(page);
      await page.getByRole('menu').getByRole('menuitem', { name: /^打开正文/ }).click();
      await page.locator('.files-panel .files-markdown.article h1', { hasText: NAME }).waitFor();
      const measured = await page.evaluate(() => {
        const bar = [...document.querySelectorAll('.composer-bar')].at(-1);
        const cut = [...(bar?.children ?? [])].filter((c) => c.scrollWidth > c.clientWidth + 1).map((c) => c.className.toString().slice(0, 30));
        const chat = [...(document.querySelector('.main')?.children ?? [])].find((c) => !c.classList.contains('files-panel'));
        return { cut, chatW: Math.round(chat?.getBoundingClientRect().width ?? 0) };
      });
      assert.deepEqual(measured.cut, [], 'no button in the composer\u2019s bottom row is cut short');
      assert.ok(measured.chatW >= 440, `the conversation keeps ${measured.chatW}px`);
      await page.locator('.files-diagram svg').waitFor({ timeout: 20_000 });
      await shot(`width-${vw}`);
    } finally { await context.close(); }
  }
});

test('writing mode on a phone: one chip beside the input, full-screen editor', { timeout: 180000 }, async () => {
  for (const colorScheme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, colorScheme });
    try {
      const page = await context.newPage();
      const shot = async (name) => shots && page.screenshot({ path: join(shots, `${prefix}-${name}-phone-${colorScheme}.png`), animations: 'disabled' });
      const title = `写作 手机 ${colorScheme}`;
      const cwd = join(daemon.workdir, `phone-${colorScheme}`);
      // A new thread, before any article exists: the chip stays one short line, not a tall card.
      await openUi(page, daemon);
      await page.goto(daemon.base + '/#/new');
      await page.getByRole('button', { name: '添加与模式', exact: true }).click();
      await page.getByRole('menuitemcheckbox', { name: /^写作模式/ }).click();
      const fresh = page.locator('.writing-bar');
      await fresh.waitFor();
      assert.ok((await fresh.boundingBox()).height < 60, `the chip line on a new thread is ${(await fresh.boundingBox()).height}px tall`);
      await shot('new');
      await page.getByRole('button', { name: '添加与模式', exact: true }).click();
      await page.getByRole('menuitemcheckbox', { name: /^写作模式/ }).click();
      await fresh.waitFor({ state: 'detached' });

      const { thread, file } = await setup(title, cwd);
      await select(page, title, true);

      await page.getByRole('button', { name: '添加与模式', exact: true }).click();
      await shot('menu');
      await page.getByRole('menuitemcheckbox', { name: /^写作模式/ }).click();
      const bar = page.locator('.writing-bar');
      await bar.waitFor();
      await send(page, '开始写');
      await waitIdle(thread.id);
      // The body is one tap from the chip itself.
      const openBody = bar.getByRole('button', { name: `打开正文 ${NAME}.md`, exact: true });
      await openBody.waitFor();
      await shot('bar');
      assert.ok(await fitsWidth(page));

      // The stage is a picker behind the chip.
      await openStages(page);
      await page.getByRole('menuitemradio', { name: '三稿' }).waitFor();
      await shot('stages');
      await page.getByRole('menuitemradio', { name: '三稿' }).click();
      await page.locator('.writing-chip', { hasText: '三稿' }).waitFor();
      assert.equal((await view(thread.id)).writing.stage, 'third');

      // The body: an article, then a full-screen page to write on.
      await openBody.click();
      const panel = page.locator('.files-panel.phone');
      await panel.locator('.files-markdown.article h1', { hasText: NAME }).waitFor();
      assert.match(await panel.locator('.article-meta').textContent(), /^三稿 · [\d,]+ 字$/);
      await panel.locator('.files-diagram svg').waitFor({ timeout: 20_000 });
      await page.waitForFunction(() => (document.querySelector('.files-inline-picture img')?.naturalWidth ?? 0) === 240);
      await shot('preview');
      const titleBox = await panel.locator('.files-title-name').boundingBox();
      assert.ok(titleBox.width >= 180, `the phone title has ${titleBox.width}px`);
      assert.equal(await panel.locator('.files-head').getByRole('button', { name: '复制路径' }).count(), 0);
      await panel.getByRole('button', { name: '文件的更多操作' }).click();
      await page.getByRole('menu', { name: '文件的更多操作' }).getByRole('menuitem', { name: '下载文件' }).waitFor();
      await shot('more');
      await page.keyboard.press('Escape');
      await panel.getByRole('button', { name: '写', exact: true }).click();
      const editor = panel.getByRole('textbox', { name: '正文', exact: true });
      await editor.fill(`${BODY}\n手机上写的一段。\n`);
      await panel.getByText('未保存', { exact: true }).waitFor();
      const box = await editor.boundingBox();
      assert.ok(box && box.width >= 380, 'the editor fills the screen');
      assert.ok((await panel.locator('.writing-paper-layer .w-mk').count()) > 0, 'the marks are dimmed');
      await shot('edit');
      await panel.getByRole('button', { name: '保存', exact: true }).click();
      await panel.getByText('未保存', { exact: true }).waitFor({ state: 'detached' });
      assert.equal(readFileSync(file, 'utf8'), `${BODY}\n手机上写的一段。\n`);

      // A passage picked out of the writing page itself: labelled with one tap, counted at the
      // panel's foot (the box is hidden behind this full-screen page), sent after going back.
      const start = BODY.indexOf('观点得是我的');
      await editor.evaluate((el, at) => {
        el.setSelectionRange(at, at + 6);
        el.dispatchEvent(new Event('select', { bubbles: true }));
      }, start);
      const docked = page.locator('.selection-menu.docked');
      await docked.waitFor();
      assert.ok((await docked.locator('.selection-tag').count()) > 0, 'the writing page offers its quick labels');
      await shot('select');
      await docked.locator('.selection-tag', { hasText: '删' }).click();
      const noted = panel.locator('.files-noted');
      await noted.waitFor();
      assert.match(await noted.textContent(), /已加 1 条注释/);
      await shot('noted');
      await noted.getByRole('button', { name: '回到对话发送' }).click();
      await panel.waitFor({ state: 'detached' });
      const strip = page.locator('.composer .note-strip');
      await strip.waitFor();
      assert.ok((await strip.textContent())?.includes(`writing/${NAME}.md`));
      await send(page, '按注释改');
      await waitIdle(thread.id);
      await page.getByText(`文件：writing/${NAME}.md`).first().waitFor();
      assert.ok(await fitsWidth(page));
    } finally { await context.close(); }
  }
});

test('late saves preserve newer typing and cannot change another file; preview refreshes after a turn', { timeout: 120000 }, async () => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  try {
    const page = await context.newPage();
    const { thread, file } = await setup('Writing races', join(daemon.workdir, 'races'));
    await api(daemon, `/api/threads/${thread.id}/writing`, { method: 'PUT', body: JSON.stringify({ stage: 'second' }) });
    const habits = join(daemon.notesDir, 'writing.md');
    mkdirSync(daemon.notesDir, { recursive: true });
    writeFileSync(habits, '# Habits\n');
    await select(page, 'Writing races', false);
    const openBody = async () => {
      await openStages(page);
      await page.getByRole('menuitem', { name: /^打开正文/ }).click();
    };
    await openBody();
    const panel = page.locator('.files-panel');
    const editor = panel.getByRole('textbox', { name: '正文', exact: true });
    await panel.getByRole('button', { name: '写', exact: true }).click();
    let release, intercepted;
    const holdSave = async () => {
      const reached = new Promise(resolve => { intercepted = resolve; });
      const gate = new Promise(resolve => { release = resolve; });
      await page.route('**/writing/text?*', async route => { intercepted(); await gate; await route.continue(); }, { times: 1 });
      return { reached };
    };
    await editor.fill('first save');
    let held = await holdSave();
    await panel.getByRole('button', { name: '保存', exact: true }).click();
    await held.reached;
    await editor.fill('newer typing');
    release();
    await waitFor(() => readFileSync(file, 'utf8') === 'first save', 'the held save committed');
    await panel.getByRole('button', { name: '保存', exact: true }).waitFor();
    assert.equal(await editor.inputValue(), 'newer typing');
    await panel.getByRole('button', { name: '保存', exact: true }).click();
    await waitFor(() => readFileSync(file, 'utf8') === 'newer typing', 'the next save uses its updated base hash');

    await editor.fill('article saved in background');
    held = await holdSave();
    await panel.getByRole('button', { name: '保存', exact: true }).click();
    await held.reached;
    await openStages(page);
    await page.getByRole('menuitem', { name: /写作习惯/ }).click();
    await panel.locator('h1', { hasText: 'Habits' }).waitFor();
    await panel.getByRole('button', { name: '写', exact: true }).click();
    await editor.fill('# My new habits\n');
    release();
    await waitFor(() => readFileSync(file, 'utf8') === 'article saved in background', 'background save committed');
    await panel.getByRole('button', { name: '保存', exact: true }).click();
    await waitFor(() => readFileSync(habits, 'utf8') === '# My new habits\n', 'the habits editor kept its own base hash');

    await panel.getByRole('button', { name: '关闭文件', exact: true }).click();
    writeFileSync(file, BODY + '\n```mermaid\nflowchart LR\n A["【待补：例子】"] --> B\n```\n');
    assert.equal(await page.evaluate(() => Object.keys(JSON.parse(localStorage.getItem('calli.writing.drafts') ?? '{}')).length), 0, 'background saves clear only their saved draft');
    await openBody();
    await page.waitForFunction(() => document.querySelectorAll('.files-diagram svg').length === 2);
    const first = await panel.locator('.files-diagram').first().innerHTML();
    await page.emulateMedia({ colorScheme: 'dark' });
    assert.equal(await panel.locator('.files-diagram svg').count(), 2, 'theme changes keep the existing drawings mounted');
    await page.waitForFunction(before => document.querySelectorAll('.files-diagram svg').length === 2 && document.querySelector('.files-diagram')?.innerHTML !== before, first);
    assert.equal(await panel.locator('.files-diagram svg').count(), 2);
    await panel.getByRole('button', { name: '关闭文件', exact: true }).click();
    await openBody();
    await page.waitForFunction(() => document.querySelectorAll('.files-diagram svg').length === 2);
    writeFileSync(file, '# Changed by the agent\n');
    await send(page, 'refresh the article');
    await waitIdle(thread.id);
    await panel.locator('h1', { hasText: 'Changed by the agent' }).waitFor();
  } finally { await context.close(); }
});
