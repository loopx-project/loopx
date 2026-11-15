// Run npm run build from the repository root before capturing screenshots.
// Captures the new UI for eyeballing: desktop and phone, light and dark.
// Run: node shots.mjs (writes PNGs into shots/)
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser } from './harness.mjs';

const outDir = join(process.cwd(), 'shots');
mkdirSync(outDir, { recursive: true });

const messagesFile = join(mkdtempSync(join(tmpdir(), 'calli-shots-')), 'messages.json');
const daemon = await startDaemon({ FAKE_PI_MESSAGES_FILE: messagesFile, CALLI_E2E_DISCOVER_AGY2: '1' }, { workdir: join(tmpdir(), 'calli-demo') });
const browser = await launchBrowser();

// Arrange: a scripted conversation played out as live turns, plus a section.
const made = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir }) })).json();
// The script is written before the first turn: the fake pi plays scripted turns live. A fresh
// fake pi names its session pi-session-1 (the daemon learns that id only after the first turn,
// so it cannot be read from the thread yet).
const turn1 = '帮我把 README 的安装步骤过一遍，确认新机器能一次跑通';;
const turn2 = '很好，跑一遍测试收尾';
writeFileSync(
  messagesFile,
  JSON.stringify({
    ['pi-session-1']: [
      { role: 'user', content: turn1 },
      { role: 'assistant', content: [
        { type: 'thinking', thinking: '先装依赖，再按 README 的步骤逐条验证，遇到缺的前提要记下来。' },
        { type: 'toolCall', id: 'c1', name: 'bash', arguments: { command: 'npm install' } },
      ] },
      { role: 'toolResult', toolCallId: 'c1', toolName: 'bash', content: [{ type: 'text', text: 'added 312 packages in 18s' }], isError: false },
      { role: 'assistant', content: [
        { type: 'toolCall', id: 'c2', name: 'read', arguments: { path: 'README.md' } },
      ] },
      { role: 'toolResult', toolCallId: 'c2', toolName: 'read', content: [{ type: 'text', text: '# Calli\n\na simple way to manage agents\n\n…安装与环境说明…' }], isError: false },
      { role: 'assistant', content: [{ type: 'text', text: '依赖装好了，README 的三步也验证过：\n\n1. `npm install` 成功，无额外系统依赖\n2. `.env` 模板字段齐全\n3. `npm run build` 通过\n\n新机器按文档操作可以一次跑通。' }] },
      { role: 'user', content: turn2 },
      { role: 'assistant', content: [
        { type: 'toolCall', id: 'c3', name: 'bash', arguments: { command: 'npm test' } },
      ] },
      { role: 'toolResult', toolCallId: 'c3', toolName: 'bash', content: [{ type: 'text', text: '174 passed, 0 failed in 3.2s' }], isError: false },
      { role: 'assistant', content: [{ type: 'text', text: '174 个测试全部通过，这轮收尾。' }] },
    ],
  }),
);
const idle = async () => {
  for (let i = 0; i < 100; i++) {
    const t = await (await api(daemon, `/api/threads/${made.id}`)).json();
    if (t.status !== 'running') return t;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error('turn never finished');
};
await api(daemon, `/api/threads/${made.id}/turns`, { method: 'POST', body: JSON.stringify({ text: turn1 }) });
await idle();
await api(daemon, `/api/threads/${made.id}/turns`, { method: 'POST', body: JSON.stringify({ text: turn2 }) });
await idle();
await api(daemon, '/api/sections', { method: 'POST', body: JSON.stringify({ name: '文档与发布' }) });

const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
await openUi(page, daemon);
await page.locator('.thread-title').first().click();
await page.waitForTimeout(1500);
// Steps unfolded: the work a turn did is the story the README should tell.
await page.locator('.work-toggle').first().click();
await page.waitForTimeout(300);
await page.locator('.step-head[aria-expanded="false"]').first().click().catch(() => {});
await page.waitForTimeout(400);
// Back to the top: with the detail card open the turn is taller than the viewport.
await page.locator('.scroller').evaluate((el) => { el.scrollTop = 0; });
await page.waitForTimeout(200);
await page.evaluate(() => document.querySelector('[data-theme]') ?? document.documentElement.setAttribute('data-theme', 'light'));
for (const theme of ['light', 'dark']) {
  await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme);
  await page.waitForTimeout(300);
  await page.screenshot({ path: join(outDir, `desktop-${theme}.png`) });
  // The add menu opened from the composer.
  await page.locator('.composer .composer-add').click();
  await page.locator('.command-menu').waitFor({ timeout: 5000 });
  await page.waitForTimeout(400);
  await page.screenshot({ path: join(outDir, `desktop-${theme}-picker.png`) });
  await page.keyboard.press('Escape');
}

const phone = await (await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })).newPage();
await openUi(phone, daemon);
await phone.locator('.menu-btn').click();
await phone.locator('.drawer-open').waitFor({ timeout: 5000 });
await phone.locator('.thread-title').first().click();
await phone.waitForTimeout(1200);
for (const theme of ['light', 'dark']) {
  await phone.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme);
  await phone.waitForTimeout(300);
  await phone.screenshot({ path: join(outDir, `phone-${theme}.png`) });
}
// The thread list: a page of its own.
await phone.locator('.menu-btn').click();
await phone.locator('.drawer-open').waitFor({ timeout: 5000 });
await phone.waitForTimeout(500);
for (const theme of ['light', 'dark']) {
  await phone.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme);
  await phone.waitForTimeout(300);
  await phone.screenshot({ path: join(outDir, `phone-${theme}-list.png`) });
}

await browser.close();
await daemon.stop();
rmSync(join(messagesFile, '..'), { recursive: true, force: true });
console.log('shots written to', outDir);
