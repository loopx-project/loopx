import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, openUi, api, launchBrowser, repoRoot } from '../harness.mjs';

let root, daemon, browser, thread, configDir;
before(async () => {
  root = mkdtempSync(join(tmpdir(), 'loochat-handover-e2e-'));
  configDir = join(root, 'claude-work');
  daemon = await startDaemon({ LOOCHAT_AGENTS_JSON: JSON.stringify({ agents: [{
    id: 'claude-work', adapter: 'claude', label: 'Claude 工作号', command: ['fake-claude'],
    env: { CLAUDE_CONFIG_DIR: configDir, FAKE_SESSION_ROOT: configDir, FAKE_ACP_SESSION: 'work-session' },
  }, {
    id: 'claude-personal', adapter: 'claude', label: 'Claude 个人号', command: ['fake-claude'],
    env: { CLAUDE_CONFIG_DIR: join(root, 'claude-personal'), FAKE_SESSION_ROOT: join(root, 'claude-personal'), FAKE_ACP_SESSION: 'personal-session' },
  }] }) });
  thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'claude-work', cwd: daemon.workdir, title: '待交接的任务' }) })).json();
  await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: '修复登录页并补测试' }) });
  browser = await launchBrowser();
});
after(async () => { await browser?.close(); await daemon?.stop(); rmSync(root, { recursive: true, force: true }); });

for (const mobile of [false, true]) for (const theme of ['light', 'dark']) {
  test(`handover summary copies cleanly from Copy submenu without spending a turn: ${mobile ? 'phone' : 'desktop'} ${theme}`, async () => {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 960 }, isMobile: mobile, hasTouch: mobile, colorScheme: theme, permissions: ['clipboard-read', 'clipboard-write'] });
    const page = await context.newPage();
    try {
      await openUi(page, daemon);
      if (mobile) await page.locator('.menu-btn').click();
      await page.locator('.sidebar .thread-item', { hasText: '待交接的任务' }).click();
      await page.getByText('echo:修复登录页并补测试', { exact: true }).waitFor();
      assert.equal(await page.getByText('unknown agent: claude', { exact: true }).count(), 0, 'custom instance ids do not query a nonexistent default');
      const before = await (await api(daemon, `/api/threads/${thread.id}/events`)).json();
      await page.getByLabel('这个对话的更多操作').click();
      await page.getByRole('menuitem', { name: '复制', exact: true }).click();
      const copyBranch = page.locator(mobile ? '.menu-float' : '.menu-branch');
      const [summaryRes] = await Promise.all([
        page.waitForResponse(r => r.url().includes(`/api/threads/${thread.id}/handover?`) && r.request().method() === 'GET'),
        copyBranch.getByRole('menuitem', { name: '复制交接信息', exact: true }).click(),
      ]);
      assert.ok(summaryRes.ok());
      const { summary } = await summaryRes.json();
      for (const value of ['Claude 工作号', thread.id, daemon.workdir, `loochat history ${thread.id}`, 'loochat handover', '修复登录页并补测试']) {
        assert.ok(summary.includes(value), value);
      }
      let clip = '';
      for (let i = 0; i < 40; i++) {
        clip = await page.evaluate(() => navigator.clipboard.readText());
        if (clip === summary) break;
        await page.waitForTimeout(50);
      }
      assert.equal(clip, summary);
      assert.deepEqual(await (await api(daemon, `/api/threads/${thread.id}/events`)).json(), before, 'copying handover info spends no turn');
    } finally { await context.close(); }
  });
}

for (const mobile of [false, true]) {
  test(`handoff continues in the same worktree on another agent without creating a second worktree: ${mobile ? 'phone' : 'desktop'}`, async () => {
    const git = (args) => execFileSync('git', args, { cwd: daemon.workdir, stdio: 'pipe' }).toString();
    git(['init', '-q']);
    git(['-c', 'user.name=Test', '-c', 'user.email=test@example.com', '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-qm', 'initial']);
    const source = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'claude-work', cwd: daemon.workdir, worktree: true, title: mobile ? '手机接手来源' : '桌面接手来源' }) })).json();
    writeFileSync(join(source.cwd, 'pending.txt'), 'preserve uncommitted work');
    await api(daemon, `/api/threads/${source.id}/turns`, { method: 'POST', body: JSON.stringify({ text: '保留工作树完成任务' }) });
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 960 }, isMobile: mobile, hasTouch: mobile, colorScheme: mobile ? 'light' : 'dark' });
    const page = await context.newPage();
    try {
      await openUi(page, daemon);
      const box = page.locator('.composer textarea');
      await box.fill('普通新对话的草稿');
      if (mobile) await page.locator('.menu-btn').click();
      const sourceRow = page.locator('.sidebar .thread-item', { hasText: source.title });
      assert.equal(await sourceRow.locator('.thread-fork').count(), 0);
      assert.ok((await sourceRow.getAttribute('title')).includes(source.worktree.branch));
      await sourceRow.click();
      await page.getByText('echo:保留工作树完成任务', { exact: true }).waitFor();
      await box.fill('原对话未发送的草稿');
      const eventsBefore = await (await api(daemon, `/api/threads/${source.id}/events`)).json();
      const treesBefore = git(['worktree', 'list', '--porcelain']);
      const idsBefore = new Set((await (await api(daemon, '/api/threads')).json()).map(t => t.id));

      await page.getByLabel('这个对话的更多操作').click();
      await page.getByRole('menuitem', { name: '接力继续', exact: true }).click();
      const dialog = page.locator('.fork-subchat-dialog');
      await dialog.waitFor();
      await dialog.locator('.fork-composer-bar .agent-btn').click();
      // Multiple accounts of the same adapter (claude) are grouped inside AgentPicker: drill into the group first.
      const personalItem = page.locator('.pop-panel .pop-item', { hasText: 'Claude 个人号' });
      if ((await personalItem.count()) === 0) {
        await page.locator('.pop-panel .pop-item', { hasText: /Claude/ }).first().click();
      }
      await personalItem.first().click();
      await dialog.locator('.fork-composer textarea').fill('先运行测试再提交');
      const [forkRes] = await Promise.all([
        page.waitForResponse(r => r.url().endsWith(`/api/threads/${source.id}/fork`) && r.request().method() === 'POST'),
        dialog.getByRole('button', { name: '接力并发送', exact: true }).click(),
      ]);
      assert.equal(forkRes.status(), 201);
      await dialog.waitFor({ state: 'detached' });

      const next = (await (await api(daemon, '/api/threads')).json()).filter(t => !idsBefore.has(t.id));
      assert.equal(next.length, 1);
      const successor = next[0];
      assert.equal(successor.cwd, source.cwd);
      assert.deepEqual(successor.worktree, source.worktree);
      assert.equal(successor.agent, 'claude-personal');
      assert.equal(successor.forkGroup, source.id);
      assert.equal(successor.forkKind, 'handoff');
      assert.deepEqual(await (await api(daemon, `/api/threads/${source.id}/events`)).json(), eventsBefore);
      assert.equal(git(['worktree', 'list', '--porcelain']), treesBefore);
      assert.equal(readFileSync(join(successor.cwd, 'pending.txt'), 'utf8'), 'preserve uncommitted work');

      let sent = [];
      for (let n = 0; n < 100; n++) {
        sent = await (await api(daemon, `/api/threads/${successor.id}/events`)).json();
        if (sent.some(e => e.type === 'turn.done')) break;
        await new Promise(r => setTimeout(r, 50));
      }
      assert.equal(sent.filter(e => e.type === 'turn.started').length, 1);
      assert.match(sent.find(e => e.type === 'turn.started')?.data.text, /<loochat-fork-anchor>/);
      assert.match(sent.find(e => e.type === 'turn.started')?.data.text, /先运行测试再提交/);
      assert.equal((await (await api(daemon, `/api/threads/${successor.id}`)).json()).agentSessionId, 'personal-session');
      assert.deepEqual(await (await api(daemon, `/api/threads/${source.id}/events`)).json(), eventsBefore);
      assert.equal(await page.evaluate(id => JSON.parse(localStorage.getItem('calli.drafts'))[id].text, source.id), '原对话未发送的草稿');
      assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('calli.drafts')).new.text), '普通新对话的草稿');
      if (process.env.LOOCHAT_SCREENSHOTS === '1') {
        const shots = join(repoRoot, 'e2e', 'shots');
        mkdirSync(shots, { recursive: true });
        await page.screenshot({ path: join(shots, `handover-${mobile ? 'phone-light' : 'desktop-dark'}-draft.png`), animations: 'disabled' });
      }
    } finally { await context.close(); }
  });
}
