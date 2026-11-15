import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { linkDaemon, startDaemon, openUi, api, launchBrowser, openCollaboration } from '../harness.mjs';

/**
 * Choosing a folder for a collaboration member on another machine: the picker browses that
 * machine, its manual-add opens that machine's roots dialog, and closing it comes back to the
 * picker with the new folder in the list — never a dead end that drops the whole choice.
 */
let root, remoteHome, added, remote, hub, browser;
before(async () => {
  root = mkdtempSync(join(tmpdir(), 'calli-collab-dirs-'));
  remoteHome = join(root, 'remote-home');
  mkdirSync(remoteHome);
  added = join(remoteHome, '手动添加的项目');
  mkdirSync(added);
  remote = await startDaemon({ HOME: remoteHome, CALLI_SOCKET: join(remoteHome, '.local/state/calli/daemon.sock') });
  mkdirSync(join(remote.workdir, '子目录'));
  hub = await startDaemon({  });
  await linkDaemon(hub, remote);
  browser = await launchBrowser();
});
after(async () => {
  await browser?.close(); await hub?.stop(); await remote?.stop();
  if (root) rmSync(root, { recursive: true, force: true });
});

async function shot(page, name) {
  if (!process.env.CALLI_E2E_SHOTS_DIR) return;
  // A shot taken mid-fade shows a half-built dialog; let the entry animation land first.
  await page.evaluate(() => Promise.all(document.getAnimations().map((a) => a.finished.catch(() => {}))));
  mkdirSync(process.env.CALLI_E2E_SHOTS_DIR, { recursive: true });
  await page.screenshot({ path: join(process.env.CALLI_E2E_SHOTS_DIR, `${name}.png`) });
}

/** An address in the one input is submitted with Enter. */
async function gotoPath(dialog, value) {
  await dialog.locator('.project-browser-input').fill(value);
  await dialog.locator('.project-browser-input').press('Enter');
}

/** A mention row for an agent on the other machine, however this screen starts collaboration. */
async function remoteMention(page, mobile, title) {
  await api(hub, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: hub.workdir, title }) });
  await openUi(page, hub);
  if (mobile) await page.locator('.menu-btn').click();
  await page.locator('.thread-row', { hasText: title }).click();
  if (!mobile) {
    await openCollaboration(page);
    await page.locator('.coord-picker-head button').click();
    await page.getByRole('button', { name: '启用协作', exact: true }).click();
    await page.locator('.composer-add.on').waitFor();
  }
  const box = page.getByRole('textbox', { name: '消息', exact: true });
  await box.fill('@');
  const list = page.getByRole('listbox', { name: '选择协作智能体' });
  await list.waitFor();
  await list.getByRole('option', { name: /pi · devbox/ }).click();
  const row = page.locator('.coord-row');
  await row.waitFor();
  return row;
}

/** The picker for that member's machine, opened from its row. */
async function openPicker(page, row) {
  // On a phone the rows fold away when the composer has no focus; a tap on the box brings them back.
  if (await row.locator('.coord-row-where').isHidden()) await page.getByRole('textbox', { name: '消息', exact: true }).click();
  await row.locator('.coord-row-where').click();
  const menu = page.getByRole('menu', { name: '成员工作目录' });
  await menu.getByRole('button', { name: '浏览这台机器的目录…' }).click();
  const dialog = page.getByRole('dialog', { name: '在哪个目录里工作？' });
  await dialog.getByText('已添加的项目位置').waitFor();
  return dialog;
}

/** The picker again: with a folder already chosen, it opens standing in that folder, not at the roots. */
async function openPickerStanding(page, row) {
  if (await row.locator('.coord-row-where').isHidden()) await page.getByRole('textbox', { name: '消息', exact: true }).click();
  await row.locator('.coord-row-where').click();
  const menu = page.getByRole('menu', { name: '成员工作目录' });
  await menu.getByRole('button', { name: '浏览这台机器的目录…' }).click();
  const dialog = page.getByRole('dialog', { name: '在哪个目录里工作？' });
  await dialog.waitFor();
  return dialog;
}

for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
  const tag = `${mobile ? 'phone' : 'desktop'}-${colorScheme}`;
  test(`manual add on another machine comes back to the picker: ${tag}`, async () => {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    const page = await context.newPage();
    page.setDefaultTimeout(10_000);
    try {
      const row = await remoteMention(page, mobile, `远程目录 ${tag}`);
      let dialog = await openPicker(page, row);
      assert.ok(await dialog.locator('.project-browser-machine', { hasText: 'devbox' }).count(), 'the picker names the machine');
      await shot(page, `rootsview-${tag}`);
      // The tree opens with the first root's children already in place: no separate "enter" step.
      await dialog.locator('.project-browser-folder', { hasText: '子目录' }).waitFor();
      // A typed address opens that folder in the tree; the home folder itself is not a project.
      await gotoPath(dialog, '~');
      await dialog.getByRole('button', { name: '添加并使用', exact: true }).waitFor();
      assert.ok(await dialog.getByRole('button', { name: '添加并使用', exact: true }).isDisabled());
      await dialog.getByRole('button', { name: '可用目录', exact: true }).click();
      await dialog.getByText('已添加的项目位置').waitFor();

      // Manual add is that machine's own roots dialog, not a dead end.
      await dialog.getByRole('button', { name: '手动添加目录…' }).click();
      const roots = page.getByRole('dialog', { name: '可用目录', exact: true });
      await roots.waitFor();
      assert.ok(await roots.locator('.project-browser-machine', { hasText: 'devbox' }).count(), 'the roots dialog names the machine too');
      await shot(page, `roots-${tag}`);
      await roots.getByLabel('要添加的目录').fill(added);
      await roots.getByRole('button', { name: '添加', exact: true }).click();
      await roots.locator('.roots-row', { hasText: '手动添加的项目' }).waitFor();
      assert.ok((await (await api(remote, '/api/config')).json()).workdirs.includes(added), 'the path was added on that machine');
      assert.ok((await (await api(hub, '/api/config')).json()).workdirs.every((path) => !path.includes('手动添加')), 'this machine\'s roots are untouched');

      // Closing it comes back to the picker, with the new folder in the added locations.
      await roots.getByRole('button', { name: '关闭', exact: true }).click();
      const addedRow = dialog.locator('.project-browser-folder', { hasText: '手动添加的项目' });
      await addedRow.waitFor();
      await shot(page, `picker-${tag}`);
      await addedRow.click();
      await dialog.getByRole('button', { name: '选择 手动添加的项目', exact: true }).click();
      await page.getByRole('dialog', { name: '在哪个目录里工作？' }).waitFor({ state: 'detached' });
      if (await row.locator('.coord-row-where').isHidden()) await page.getByRole('textbox', { name: '消息', exact: true }).click();
      await row.locator('.coord-row-where', { hasText: '手动添加的项目' }).waitFor();

      // The next picker for that machine starts from the fresh list, not the copy from before. It
      // opens standing in the chosen folder (that is the mention's folder now); the added locations
      // behind the "可用目录" step list the new folder.
      dialog = await openPickerStanding(page, row);
      await dialog.getByRole('button', { name: '可用目录', exact: true }).click();
      await dialog.locator('.project-browser-folder', { hasText: '手动添加的项目' }).waitFor();
      await dialog.getByRole('button', { name: '取消', exact: true }).click();
    } finally { await context.close(); }
  });
}

for (const mobile of [false, true]) for (const colorScheme of ['light', 'dark']) {
  const tag = `${mobile ? 'phone' : 'desktop'}-${colorScheme}`;
  test(`a member on another machine takes a model, and the row keeps its layout: ${tag}`, async () => {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1400, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme });
    const page = await context.newPage();
    page.setDefaultTimeout(10_000);
    try {
      const row = await remoteMention(page, mobile, `远程模型 ${tag}`);
      // The agent's own name leads; the machine and folder sit in one place chip, not in the name.
      assert.equal((await row.locator('.coord-row-name').innerText()).trim(), '@pi');
      assert.match(await row.locator('.coord-row-where').innerText(), /devbox/);
      const quiet = row.getByRole('button', { name: /的模型与思考强度$/ });
      await quiet.waitFor();
      await quiet.click();
      const control = page.locator('.model-control-panel');
      const toModels = control.getByRole('button', { name: /切换模型/ });
      await control.getByRole('menuitemradio').or(toModels).first().waitFor();
      if (await toModels.count()) await toModels.click();
      const choice = control.getByRole('menuitemradio').nth(1);
      const picked = (await choice.locator('.pop-item-label').innerText()).trim();
      await choice.click();
      await page.keyboard.press('Escape');
      await control.waitFor({ state: 'detached' });
      await row.locator('.member-model.set').waitFor();
      if (!mobile) assert.match(await row.locator('.member-model').innerText(), new RegExp(picked.slice(0, 6)));
      // Nothing overflows the box, however narrow it is.
      const fits = await row.evaluate((el) => [...el.children].every((c) => c.getBoundingClientRect().right <= el.getBoundingClientRect().right + 1));
      assert.ok(fits, 'the row\'s parts stay inside it');
      // Long names give way to ellipses instead of pushing the controls or the task out of the row.
      await row.evaluate((el) => {
        el.querySelector('.coord-row-name').textContent = '@Claude Code Extra Long Instance Name';
        el.querySelector('.coord-row-machine').textContent = 'Magpie Production Gateway Machine';
      });
      assert.ok(await row.evaluate((el) => [...el.children].every((c) => c.getBoundingClientRect().right <= el.getBoundingClientRect().right + 1)), 'long names stay inside the row');
      assert.ok((await row.getByRole('textbox').boundingBox()).width >= 100, 'the task keeps a usable width');
      await shot(page, `remote-row-${tag}`);
    } finally { await context.close(); }
  });
}
