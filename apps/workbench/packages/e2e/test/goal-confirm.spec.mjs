import { test } from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { api, launchBrowser, openUi, repoRoot, startDaemon } from '../harness.mjs';

test('cancelling Goal confirmation preserves the draft without reporting a send failure', async () => {
  const daemon = await startDaemon({ LOOCHAT_AGENTS_JSON: JSON.stringify({ agents: [{
    id: 'codex', adapter: 'codex', command: [process.execPath, join(repoRoot, 'daemon/test/fixtures/fake-codex.mjs')],
  }] }) });
  let browser;
  try {
    browser = await launchBrowser();
    for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme });
      try {
        const page = await context.newPage();
        const errors = []; page.on('pageerror', error => errors.push(error.message));
        let applied = 0;
        let releaseApply;
        const applyResponse = new Promise(resolve => { releaseApply = resolve; });
        await page.route('**/api/loopx/capabilities', route => route.fulfill({ json: { agents: [{ agent: 'codex', available: true, permissions: [{ id: 'read_only', available: true, isDefault: true }] }] } }));
        await page.route('**/api/loopx/workspaces', route => route.fulfill({ json: { workspaces: [{ ref: 'fixture-workspace', title: 'Review workspace', path: daemon.workdir }] } }));
        await page.route('**/api/loopx/launch/preview', route => route.fulfill({ json: { proposalId: 'proposal-fixture', goalId: 'goal-fixture', title: 'Review release', workspace: daemon.workdir, agent: 'codex', goalAgent: 'codex', permission: 'read_only' } }));
        await page.route('**/api/loopx/launch/apply', async route => { applied++; await applyResponse; return route.fulfill({ status: 503, json: { error: 'Fixture create failure' } }); });
        await openUi(page, daemon);
        const box = page.locator('.composer textarea');
        const draft = `Review release ${mobile} ${colorScheme}`;
        await box.fill(draft);
        await page.getByRole('button', { name: '交给 LoopX 持续跑', exact: true }).click();
        const dialog = page.getByRole('dialog', { name: '创建 LoopX Goal', exact: true });
        const open = async () => {
          await page.locator('.composer .send-btn').click();
          await dialog.getByText('核心已检查：Review release', { exact: true }).waitFor();
        };
        const cancelled = async () => {
          await dialog.waitFor({ state: 'detached' });
          await page.waitForFunction(() => !document.querySelector('.composer .send-btn')?.disabled);
          assert.equal(await page.locator('.banner[role=alert]').count(), 0, 'cancellation is not a delivery error');
          assert.equal(await box.inputValue(), draft);
          const saved = await page.evaluate(() => Object.values(JSON.parse(localStorage.getItem('calli.drafts') ?? '{}')));
          assert.ok(saved.some(value => value.text === draft && value.sending === undefined), 'the retained draft is not marked in flight');
        };
        await open();
        await dialog.getByRole('button', { name: '取消', exact: true }).click();
        await cancelled();
        await open();
        await page.keyboard.press('Escape');
        await cancelled();
        assert.equal(applied, 0, 'cancelling never creates a Goal');
        // A real apply failure remains visible; cancelling its review still leaves a reusable draft.
        await open();
        await dialog.locator('.dialog-actions .primary').click();
        await page.waitForFunction(() => document.querySelector('.goal-confirm .dialog-actions .primary')?.disabled);
        assert.equal(await dialog.getByRole('button', { name: '取消', exact: true }).isDisabled(), true, 'an already submitted creation cannot be cancelled locally');
        await page.keyboard.press('Escape');
        assert.equal(await dialog.isVisible(), true);
        releaseApply();
        await dialog.getByRole('alert').getByText('Fixture create failure', { exact: true }).waitFor();
        assert.equal(applied, 1);
        await dialog.getByRole('button', { name: '取消', exact: true }).click();
        await cancelled();
        await page.reload();
        await box.waitFor();
        assert.equal(await box.inputValue(), draft, 'cancellation preserves the draft across reload');
        assert.deepEqual(await (await api(daemon, '/api/threads')).json(), []);
        assert.deepEqual(errors, []);
      } finally { await context.close(); }
    }
  } finally { await browser?.close(); await daemon.stop(); }
});
