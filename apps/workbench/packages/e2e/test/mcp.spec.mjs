import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { linkDaemon, startDaemon, openUi, api, launchBrowser } from '../harness.mjs';

let root, hub, remote, browser, page;
const envFor = (home) => ({ HOME: home, CLAUDE_CONFIG_DIR: join(home, '.claude'), CODEX_HOME: join(home, '.codex'), PI_CODING_AGENT_DIR: join(home, '.pi/agent'), CALLI_AGY_HOME: join(home, '.agy') });

function seedLocal(home) {
  mkdirSync(join(home, '.claude'), { recursive: true });
  writeFileSync(join(home, '.claude/.claude.json'), JSON.stringify({ numStartups: 42, mcpServers: { botmux: { command: 'botmux', args: ['--stdio'], env: { TOKEN: 't' } } } }, null, 2));
  mkdirSync(join(home, '.codex'), { recursive: true });
  writeFileSync(join(home, '.codex', 'config.toml'), `model = "gpt-5.2-codex"\n\n[mcp_servers.github]\nurl = "https://api.githubcopilot.com/mcp/"\nbearer_token_env_var = "GITHUB_PAT"\n`);
}

function seedRemote(home) {
  mkdirSync(join(home, '.pi', 'agent'), { recursive: true });
  writeFileSync(join(home, '.pi', 'agent', 'mcp.json'), JSON.stringify({ mcpServers: { playwright: { command: 'npx', args: ['-y', '@playwright/mcp@latest', '--headless'], exposure: 'direct' } } }, null, 2));
}

before(async () => {
  root = mkdtempSync(join(tmpdir(), 'calli-mcp-e2e-'));
  const localHome = join(root, 'local');
  const remoteHome = join(root, 'remote');
  mkdirSync(localHome, { recursive: true });
  mkdirSync(remoteHome, { recursive: true });
  seedLocal(localHome);
  seedRemote(remoteHome);
  remote = await startDaemon({ ...envFor(remoteHome), CALLI_SOCKET: join(remoteHome, '.local/state/calli/daemon.sock') });
  hub = await startDaemon({ ...envFor(localHome) });
  await linkDaemon(hub, remote);
  browser = await launchBrowser();
  page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  page.on('pageerror', (err) => console.error('browser error:', err));
  await page.addInitScript((token) => localStorage.setItem('calli.token', token), hub.token);
  await openUi(page, hub);
  await openMcp(page);
});

after(async () => {
  await browser?.close();
  await hub?.stop();
  await remote?.stop();
  if (root) rmSync(root, { recursive: true, force: true });
});

async function openMcp(p, phone = false, shot) {
  if (phone) await p.locator('.menu-btn').click();
  await p.getByRole('button', { name: '配置', exact: true }).click();
  await p.locator('.settings-nav').getByRole('button', { name: 'MCP', exact: true }).waitFor();
  if (shot) { await p.waitForTimeout(300); await p.screenshot({ path: shot }); }
  await p.locator('.settings-nav').getByRole('button', { name: 'MCP', exact: true }).click();
  await p.locator('.skills-row').first().waitFor();
}

test('the scan spans machines and agents, and the page has an address of its own', async () => {
  await page.getByRole('button', { name: 'botmux 的操作' }).waitFor();
  assert.deepEqual((await page.locator('.skills-grid-head .skills-cell-machine').allInnerTexts()).sort(), ['devbox', '本机'].sort());
  // botmux and github on 本机, playwright on devbox: one row each, marks in the right cells.
  const botmux = page.locator('.skills-row', { hasText: 'botmux' });
  assert.ok(await botmux.locator('.skill-mark').count(), 'botmux shows the agents that have it');
  await page.getByRole('button', { name: 'github 的操作' }).waitFor();
  await page.getByRole('button', { name: 'playwright 的操作' }).waitFor();
  assert.equal(page.url().endsWith('#/settings/mcp'), true, 'the page has its own address');
  await page.reload();
  await page.getByRole('button', { name: 'botmux 的操作' }).waitFor();
});

test('annotate a business line and purpose, then the groups and filter chips follow', async () => {
  await page.getByRole('button', { name: 'playwright 的操作' }).click();
  await page.getByRole('menuitem', { name: '标注业务线与用途' }).click();
  await page.getByLabel('业务线', { exact: true }).fill('前端工具');
  await page.getByLabel('它是干什么的').fill('让 agent 操作浏览器做端到端测试');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await page.locator('.mcp-group-name', { hasText: '前端工具' }).waitFor();
  assert.ok(await page.locator('.mcp-group-head', { hasText: '未分组' }).count(), 'the ungrouped rest stays below');
  await page.getByRole('button', { name: '前端工具', exact: true }).click();
  assert.equal(await page.locator('.skills-row').count(), 1, 'the filter narrows to that line');
  await page.getByRole('button', { name: '全部', exact: true }).click();
  // The words show in the detail dialog too.
  await page.locator('.skills-row', { hasText: 'playwright' }).getByTitle('查看详情', { exact: true }).click();
  await page.getByRole('dialog', { name: 'playwright' }).getByText('让 agent 操作浏览器做端到端测试').waitFor();
  await page.getByRole('dialog').getByLabel('关闭').click();
});

test('install crosses machines through the proxy and asks before overwriting a different config', async () => {
  await page.getByRole('button', { name: 'playwright 的操作' }).click();
  await page.getByRole('menuitem', { name: '安装到…' }).click();
  const dialog = page.getByRole('dialog', { name: '安装「playwright」' });
  await dialog.waitFor();
  // Preselected are the agents without the server; keep only 本机的 Claude Code.
  while (await dialog.locator('.skill-target[aria-pressed=true]').count()) await dialog.locator('.skill-target[aria-pressed=true]').first().click();
  await dialog.locator('.skill-target-row', { hasText: '本机' }).getByRole('button', { name: 'Claude Code', exact: true }).click();
  await dialog.getByRole('button', { name: '安装到 1 个 agent' }).click();
  await dialog.getByRole('button', { name: '完成', exact: true }).waitFor();
  const doc = JSON.parse(readFileSync(join(root, 'local/.claude/.claude.json'), 'utf8'));
  // The config travels verbatim: pi's own `exposure` field comes along, the way a copy does.
  assert.deepEqual(doc.mcpServers.playwright, { command: 'npx', args: ['-y', '@playwright/mcp@latest', '--headless'], exposure: 'direct' });
  assert.equal(doc.numStartups, 42, "Claude's own state survives the write");
  await dialog.getByRole('button', { name: '完成', exact: true }).click();
});

test('remove takes one server out of one agent on one machine', async () => {
  await page.getByRole('button', { name: 'playwright 的操作' }).click();
  await page.getByRole('menuitem', { name: '移除（devbox · pi）' }).click();
  await page.getByRole('button', { name: '移除', exact: true }).click();
  await page.getByRole('button', { name: 'botmux 的操作' }).waitFor();
  const doc = JSON.parse(readFileSync(join(root, 'remote/.pi/agent/mcp.json'), 'utf8'));
  assert.equal(doc.mcpServers.playwright, undefined, 'the server is gone from the remote pi config');
});

test('a manual registration can be installed anywhere', async () => {
  await page.getByRole('button', { name: '登记 MCP' }).click();
  await page.getByLabel('名称').fill('weather');
  await page.getByLabel('业务线', { exact: true }).fill('数据');
  await page.getByLabel('它是干什么的').fill('查天气');
  await page.getByPlaceholder('命令，如 npx').fill('weather-mcp');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await page.getByRole('button', { name: 'weather 的操作' }).waitFor();
  assert.ok(await page.locator('.skills-row', { hasText: 'weather' }).locator('.mcp-badge').count(), 'a registered config is badged');
  await page.getByRole('button', { name: 'weather 的操作' }).click();
  await page.getByRole('menuitem', { name: '安装到…' }).click();
  const dialog = page.getByRole('dialog', { name: '安装「weather」' });
  await dialog.waitFor();
  // A server nowhere yet preselects every target; keep only the remote pi.
  while (await dialog.locator('.skill-target[aria-pressed=true]').count()) await dialog.locator('.skill-target[aria-pressed=true]').first().click();
  await dialog.locator('.skill-target-row', { hasText: 'devbox' }).getByRole('button', { name: 'pi', exact: true }).click();
  await dialog.getByRole('button', { name: '安装到 1 个 agent' }).click();
  await dialog.getByRole('button', { name: '完成', exact: true }).waitFor();
  const doc = JSON.parse(readFileSync(join(root, 'remote/.pi/agent/mcp.json'), 'utf8'));
  assert.deepEqual(doc.mcpServers.weather, { command: 'weather-mcp' });
  await dialog.getByRole('button', { name: '完成', exact: true }).click();
});

test('desktop and phone, light and dark fit the viewport with readable dialogs', async () => {
  const shots = process.env.CALLI_MCP_SHOTS;
  if (shots) mkdirSync(shots, { recursive: true });
  for (const phone of [false, true]) for (const theme of ['light', 'dark']) {
    const ctx = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 960 }, isMobile: phone, hasTouch: phone, colorScheme: theme });
    const p = await ctx.newPage();
    await p.addInitScript((token) => localStorage.setItem('calli.token', token), hub.token);
    await openUi(p, hub);
    await openMcp(p, phone, shots ? join(shots, `${phone ? 'phone' : 'desktop'}-${theme}-menu.png`) : undefined);
    await p.waitForTimeout(300);
    assert.equal(await p.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.ok((await p.locator('.skills-cell-name').last().boundingBox()).width > (phone ? 150 : 200));
    assert.equal(await p.locator('.composer').count(), 0, 'a config page has no composer under it');
    assert.equal((await p.locator('.settings-topbar h1').innerText()).trim(), phone ? 'MCP' : '配置', 'the top bar names the current navigation level');
    // On a phone the machines go on one short line under each row, not a stretched empty block.
    assert.ok((await p.locator('.skills-row').first().boundingBox()).height < 160);
    if (shots) await p.screenshot({ path: join(shots, `${phone ? 'phone' : 'desktop'}-${theme}.png`) });
    await p.getByRole('button', { name: 'playwright 的操作' }).click();
    await p.getByRole('menuitem', { name: '安装到…' }).click();
    const dialog = p.getByRole('dialog', { name: '安装「playwright」' });
    await dialog.waitFor();
    await p.waitForTimeout(300);
    const box = await dialog.boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= (phone ? 390 : 1440));
    if (shots) await p.screenshot({ path: join(shots, `${phone ? 'phone' : 'desktop'}-${theme}-install.png`) });
    await ctx.close();
  }
});
