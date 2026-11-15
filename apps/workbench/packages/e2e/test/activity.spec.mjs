// What an answer points at, and the list by activity: an SVG the agent drew shows as a picture,
// whether it saved the file or pasted the source; a link to a file on the host never takes the
// browser away from the thread; and the sidebar can be read by what is going on instead of by project.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let daemon;
let browser;
let page;

before(async () => {
  daemon = await startDaemon();
  browser = await launchBrowser();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, acceptDownloads: true });
  page = await ctx.newPage();
});

after(async () => {
  await browser?.close();
  await daemon?.stop();
});

const post = (path, body) => api(daemon, path, { method: 'POST', body: JSON.stringify(body) });

/** A pi thread with one turn; the fake agent answers "hi there <text>". Waits for the turn unless told not to. */
async function thread(text, wait = true) {
  const made = await (await post('/api/threads', { agent: 'pi', cwd: daemon.workdir })).json();
  await post(`/api/threads/${made.id}/turns`, { text });
  for (let i = 0; wait && i < 100; i++) {
    if ((await (await api(daemon, `/api/threads/${made.id}`)).json()).status !== 'running') break;
    await new Promise((r) => setTimeout(r, 100));
  }
  return made.id;
}

test('an SVG the answer links shows as a picture, and no link leaves the thread', async () => {
  // Drawn the way an agent draws: no size of its own, no background, and a script that must not run.
  writeFileSync(
    join(daemon.workdir, 'dino.svg'),
    '<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 800" width="100%" height="100%"><script>window.ran = 1</script><circle cx="600" cy="400" r="300"/></svg>',
  );
  mkdirSync(join(daemon.workdir, 'src'));
  writeFileSync(join(daemon.workdir, 'src', 'notes.md'), '# notes');
  await thread(`saved to:\n👉 [dino.svg](file://${daemon.workdir}/dino.svg)\n\nnotes in [the notes](src/notes.md), more at [the site](https://example.com/)`);

  await openUi(page, daemon);
  await page.locator('.thread-item', { hasText: 'saved to' }).click();
  const thumb = page.locator('.file-image img');
  await thumb.waitFor({ timeout: 10_000 });
  // A data: address, not a blob: one that would run the script if opened in a tab of its own.
  assert.match(await thumb.getAttribute('src'), /^data:image\/svg\+xml;base64,/);
  assert.ok((await thumb.boundingBox()).width > 300, 'drawn at a readable size although it names none');

  let left = false;
  page.on('framenavigated', (frame) => frame === page.mainFrame() && (left = true));

  await page.locator('.markdown a', { hasText: 'dino.svg' }).click();
  await page.locator('.lightbox img').waitFor({ timeout: 5000 });
  assert.equal(await page.evaluate(() => window.ran), undefined);
  await page.keyboard.press('Escape');
  await page.locator('.lightbox').waitFor({ state: 'detached' });

  // Not a picture: the path goes to the clipboard, since there is nowhere to go.
  await page.locator('.markdown a', { hasText: 'the notes' }).click();
  await page.locator('.file-link-note', { hasText: '已复制路径' }).waitFor({ timeout: 3000 });

  const web = page.locator('.markdown a', { hasText: 'the site' });
  assert.equal(await web.getAttribute('href'), 'https://example.com/');
  assert.equal(await web.getAttribute('target'), '_blank');
  assert.match(await web.getAttribute('rel'), /noopener/);

  assert.equal(left, false, 'the page stayed where it was');
  assert.equal(await thumb.count(), 1);
});

test('SVG source pasted in a code block is drawn, with the code one click away', async () => {
  const fence = '```';
  await thread(
    [
      'drawn:',
      `${fence}xml\n<svg viewBox="0 0 1200 800" width="100%" height="100%"><script>window.ran = 2</script><circle cx="600" cy="400" r="300"/></svg>\n${fence}`,
      'an icon:',
      `${fence}svg\n<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48"><circle cx="24" cy="24" r="20"/></svg>\n${fence}`,
      'not well formed:',
      `${fence}svg\n<svg><g></svg>\n${fence}`,
      'not a drawing:',
      `${fence}ts\nconst a = 1;\n${fence}`,
    ].join('\n\n'),
  );
  await page.reload();
  await page.locator('.thread-item', { hasText: 'drawn:' }).click();
  const blocks = page.locator('.row.assistant .code-block');
  const first = blocks.first();
  await first.locator('.code-picture img').waitFor({ timeout: 10_000 });
  await page.waitForTimeout(300); // the one that cannot be drawn falls back once the browser has tried
  assert.equal(await blocks.count(), 4);
  assert.deepEqual(await blocks.evaluateAll((els) => els.map((el) => (el.querySelector('.code-picture') ? 'picture' : 'code'))), ['picture', 'picture', 'code', 'code']);
  assert.equal(await blocks.nth(3).locator('.code-views').count(), 0, 'no switch where there is nothing to draw');
  // A drawing with no size fills the block; one with a size keeps it.
  assert.ok((await first.locator('img').boundingBox()).width > 400);
  assert.equal(Math.round((await blocks.nth(1).locator('img').boundingBox()).width), 48);
  assert.equal(await page.evaluate(() => window.ran), undefined, 'drawn in an <img>: nothing in it runs');

  await first.getByRole('button', { name: '代码', exact: true }).click();
  assert.match(await first.locator('pre').innerText(), /^<svg viewBox/);
  assert.equal(await first.locator('.code-picture').count(), 0);
  await first.getByRole('button', { name: '预览', exact: true }).click();
  await first.locator('.code-picture').click();
  const full = page.locator('.lightbox img');
  await full.waitFor({ timeout: 5000 });

  // At full size: it rests above the bar, the wheel zooms towards the pointer, a drag moves it.
  const scale = page.locator('.lightbox-scale');
  const rest = await full.boundingBox();
  const bar = await page.locator('.lightbox-bar').boundingBox();
  assert.ok(rest.width > 600 && rest.y + rest.height <= bar.y, 'a drawing with no size takes the room, and no more');
  await page.mouse.move(rest.x + rest.width * 0.75, rest.y + rest.height * 0.25);
  await page.mouse.wheel(0, -600);
  await page.waitForFunction(() => parseInt(document.querySelector('.lightbox-scale').textContent) > 150);
  const zoomed = await full.boundingBox();
  await page.mouse.down();
  await page.mouse.move(rest.x + rest.width * 0.75 - 120, rest.y + rest.height * 0.25 + 60, { steps: 4 });
  await page.mouse.up();
  const dragged = await full.boundingBox();
  assert.deepEqual([Math.round(dragged.x - zoomed.x), Math.round(dragged.y - zoomed.y)], [-120, 60]);
  // Neither the drag nor a click on the zoomed picture closes it; back at rest, a click does.
  await page.mouse.click(rest.x + rest.width / 2, rest.y + rest.height / 2);
  assert.equal(await page.locator('.lightbox').count(), 1);
  await page.getByRole('button', { name: '恢复原大小' }).click();
  assert.equal(await scale.innerText(), '100%');

  // Saved as the drawing it is, or as a bitmap of it.
  const saved = async (label) => {
    const [download] = await Promise.all([page.waitForEvent('download', { timeout: 15_000 }), page.getByRole('button', { name: label }).click()]);
    const file = join(daemon.dataDir, download.suggestedFilename());
    await download.saveAs(file);
    return { name: download.suggestedFilename(), bytes: readFileSync(file) };
  };
  const asSvg = await saved('下载 SVG');
  assert.equal(asSvg.name, 'drawing.svg');
  assert.match(asSvg.bytes.toString('utf8'), /^<svg xmlns="http:\/\/www\.w3\.org\/2000\/svg" viewBox="0 0 1200 800"/, 'with the namespace an <img> needed, so the file opens anywhere');
  const asPng = await saved('下载 PNG');
  assert.equal(asPng.name, 'drawing.png');
  assert.deepEqual([...asPng.bytes.subarray(1, 4)], [0x50, 0x4e, 0x47]);
  assert.deepEqual([asPng.bytes.readUInt32BE(16), asPng.bytes.readUInt32BE(20)], [2048, 1365], 'drawn large: a vector has no pixels of its own');

  await page.mouse.click(rest.x + rest.width / 2, rest.y + rest.height / 2);
  await page.locator('.lightbox').waitFor({ state: 'detached' });
  await page.waitForTimeout(200);
  assert.equal(await page.locator('.lightbox').count(), 0, 'the click that closed it did not land on the picture underneath');
});

test('the list by activity: what wants attention first, then by day; the choice is kept', async () => {
  const old = await thread('last week');
  await api(daemon, `/api/threads/${old}`, { method: 'PATCH', body: JSON.stringify({ unread: false }) });
  const db = new DatabaseSync(join(daemon.dataDir, 'hub.db'));
  db.prepare('UPDATE threads SET updated_at = ? WHERE id = ?').run(new Date(Date.now() - 5 * 86_400_000).toISOString(), old);
  db.close();
  await thread('finished unseen');
  await thread('slow still going', false);

  await page.reload();
  const toggle = page.getByRole('button', { name: '按活动查看' });
  await toggle.waitFor({ timeout: 10_000 });
  assert.equal(await toggle.getAttribute('aria-pressed'), 'false');
  assert.equal(await toggle.locator('.activity-badge').count(), 0, 'no dot on the button');
  assert.match(await toggle.getAttribute('title'), /1 个线程有新动静/, 'a thread finished out of sight is counted in the hint');
  assert.ok(await page.locator('.group-head').count(), 'by project to begin with');

  await toggle.click();
  const list = page.locator('.thread-groups.activity');
  await list.waitFor();
  assert.equal(await toggle.getAttribute('aria-pressed'), 'true');
  const groups = await list.locator('.thread-group').evaluateAll((els) =>
    els.map((el) => [el.querySelector('.group-label').textContent, [...el.querySelectorAll('.thread-title')].map((t) => t.textContent)]),
  );
  assert.equal(groups[0][0], '优先级');
  assert.deepEqual(groups[0][1].slice(-2), ['finished unseen', 'slow still going'], 'unread before running');
  assert.deepEqual(groups.at(-1), ['过去 7 天', ['last week']]);
  assert.ok(groups.some(([label]) => label === '今天') || groups.length === 2);
  // Out of its project group, a row names the project.
  assert.equal(await list.locator('.thread-row', { hasText: 'last week' }).locator('.thread-dir').count(), 1);

  await page.reload();
  await page.locator('.thread-groups.activity').waitFor({ timeout: 10_000 });
  await page.getByRole('button', { name: '按活动查看' }).click();
  await page.locator('.group-head').first().waitFor();
  assert.equal(await page.locator('.thread-groups.activity').count(), 0);
});
