import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { startDaemon, launchBrowser, openUi, api, closeSettings } from '../harness.mjs';

const navigate = (page, path) => page.evaluate(hash => { location.hash = hash; }, path);
const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

test('settings pages preserve navigation, guard drafts and load details on entry across screen sizes and themes', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  const shots = '/tmp/calli-settings-navigation'; mkdirSync(shots, { recursive: true });
  try {
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: 'Navigation return' }) })).json();
    await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: Array.from({ length: 50 }, (_, i) => `Review paragraph ${i + 1}`).join('\n\n') }) });
    for (const mobile of [false, true]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme: theme });
      const page = await context.newPage(); page.setDefaultTimeout(10000);
      const errors = []; const requests = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('request', req => requests.push(new URL(req.url()).pathname + new URL(req.url()).search));
      await openUi(page, daemon);
      await page.addStyleTag({ content: '*,*::before,*::after{animation:none!important;transition:none!important}' });
      if (mobile) await page.getByRole('button', { name: '打开线程列表', exact: true }).click();
      await page.locator('.sidebar').getByRole('button', { name: '配置', exact: true }).click();
      await page.locator('.settings-page').waitFor();
      await page.waitForFunction(() => Math.abs(document.querySelector('.main').getBoundingClientRect().x - document.querySelector('.shell').getBoundingClientRect().x) < 2);
      assert.equal(await page.locator('.sidebar').isVisible(), false, 'settings replaces the conversation sidebar');
      assert.equal(await page.locator('.sidebar-resize-handle').isVisible(), false);
      assert.ok(Math.abs((await page.locator('.settings-page').boundingBox()).width - await page.locator('.shell').evaluate(el => el.clientWidth)) < 1, 'settings fills the application sheet');
      if (!mobile) await page.keyboard.press('Control+Shift+S');
      else {
        const touch = await context.newCDPSession(page);
        await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: 6, y: 300 }] });
        await touch.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: 160, y: 300 }] });
        await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
        assert.equal(await page.locator('.drawer-open').count(), 0, 'settings does not open the conversation sidebar with an edge swipe');
        await touch.detach();
      }
      assert.equal(await page.getByRole('dialog').count(), 0);
      await page.screenshot({ path: `${shots}/index-${mobile ? 'phone' : 'desktop'}-${theme}.png`, animations: 'disabled' });
      await page.locator('.settings-nav').getByRole('button', { name: '常规', exact: true }).click();
      const general = page.getByRole('region', { name: '常规', exact: true });
      await general.getByRole('button', { name: '默认打开页面', exact: true }).click();
      const menu = page.getByRole('menu', { name: '默认打开页面', exact: true });
      const rect = await menu.boundingBox();
      assert.ok(rect.x >= 0 && rect.y >= 0 && rect.x + rect.width <= (mobile ? 390 : 1440), 'anchored menu fits the viewport');
      assert.equal(await page.locator('.pop-scrim').count(), 0);
      await page.keyboard.press('Escape');
      await general.waitFor();
      await navigate(page, '#/settings/push');
      const push = page.getByRole('region', { name: '推送', exact: true });
      const toggle = push.getByRole('switch', { name: '一轮失败', exact: true });
      await toggle.waitFor();
      const initial = await toggle.getAttribute('aria-checked');
      assert.equal(requests.filter(path => path === '/api/push/status').length, 0, 'full push records stay unloaded');
      await toggle.click();
      await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
      const warning = page.getByRole('dialog', { name: '保留这次修改？', exact: true });
      await warning.getByRole('button', { name: '取消', exact: true }).click();
      assert.notEqual(await toggle.getAttribute('aria-checked'), initial);
      await page.route('**/api/push/settings', route => route.request().method() === 'PUT' ? route.fulfill({ status: 500, json: { error: 'Save failed for test' } }) : route.continue());
      await push.getByRole('button', { name: '保存', exact: true }).click();
      await push.getByRole('alert').filter({ hasText: 'Save failed for test' }).waitFor();
      await page.unroute('**/api/push/settings');
      await push.getByRole('button', { name: '保存', exact: true }).click();
      await push.getByRole('status').filter({ hasText: '已保存' }).waitFor();
      await push.getByRole('button', { name: /^最近/ }).click();
      await page.waitForFunction(() => Boolean(document.querySelector('.push-records')));
      assert.equal(requests.filter(path => path === '/api/push/status').length, 1);
      await page.screenshot({ path: `${shots}/push-${mobile ? 'phone' : 'desktop'}-${theme}.png`, animations: 'disabled' });
      await navigate(page, '#/settings/handover');
      const auto = page.getByRole('region', { name: '自动交接', exact: true });
      const compactToggle = auto.getByRole('switch', { name: '到达阈值前自动压缩上下文', exact: true });
      await compactToggle.waitFor();
      const initialCompact = await compactToggle.getAttribute('aria-checked');
      await compactToggle.click();
      await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
      await warning.getByRole('button', { name: '取消', exact: true }).click();
      assert.notEqual(await compactToggle.getAttribute('aria-checked'), initialCompact, 'compression edits use the same draft guard');
      await page.route('**/api/autocompact-settings', route => route.request().method() === 'PUT' ? route.fulfill({ status: 500, json: { error: 'Compression save failed for test' } }) : route.continue());
      const handoverSaves = requests.filter(path => path === '/api/handover-settings').length;
      await auto.getByRole('button', { name: '保存交接设置', exact: true }).click();
      await auto.getByRole('alert').filter({ hasText: 'Compression save failed for test' }).waitFor();
      assert.equal(requests.filter(path => path === '/api/handover-settings').length, handoverSaves, 'a failed compression save does not continue and report success');
      await page.unroute('**/api/autocompact-settings');
      await auto.getByRole('button', { name: '保存交接设置', exact: true }).click();
      await auto.getByRole('status').filter({ hasText: '已保存' }).waitFor();
      const storedCompact = await (await api(daemon, '/api/autocompact-settings')).json();
      assert.equal(storedCompact.settings.enabled, initialCompact !== 'true');
      await navigate(page, '#/settings/assistant');
      await page.locator('.settings-related').waitFor();
      const profilePath = '/api/personal/profile';
      const before = requests.filter(path => path === profilePath).length;
      await page.getByRole('button', { name: '我在意什么', exact: true }).click();
      await page.locator('.profile-field').waitFor();
      assert.equal(requests.filter(path => path === profilePath).length, before + 1);
      await page.locator('.profile-field').fill('Unsaved profile draft');
      await page.goBack();
      await warning.waitFor();
      await warning.getByRole('button', { name: '取消', exact: true }).click();
      assert.equal(await page.locator('.profile-field').inputValue(), 'Unsaved profile draft');
      assert.ok(page.url().endsWith('#/settings/assistant/profile'));
      await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
      await warning.getByRole('button', { name: '放弃修改', exact: true }).click();
      await page.locator('.settings-related').waitFor();
      await navigate(page, '#/settings/machines');
      await page.getByRole('button', { name: '添加机器', exact: true }).waitFor();
      await page.screenshot({ path: `${shots}/machines-${mobile ? 'phone' : 'desktop'}-${theme}.png`, animations: 'disabled' });
      // Going into settings must preserve the active conversation, draft and reading position.
      await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
      if (mobile) {
        await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
        assert.equal(await page.locator('.drawer-open').count(), 1, 'settings opened from the list go back to the list');
      }
      await page.locator('.sidebar .thread-item', { hasText: 'Navigation return' }).click();
      await page.locator('.composer textarea').fill('Draft survives settings');
      await page.waitForFunction(() => document.querySelector('.scroller')?.scrollHeight > innerHeight);
      await page.locator('.scroller').hover();
      await page.mouse.wheel(0, -400);
      await page.waitForFunction(() => { const el = document.querySelector('.scroller'); return el.scrollHeight - el.scrollTop - el.clientHeight >= 390; });
      const scroll = await page.locator('.scroller').evaluate(el => el.scrollTop);
      if (mobile) await page.getByRole('button', { name: '打开线程列表', exact: true }).click();
      await page.locator('.sidebar').getByRole('button', { name: '配置', exact: true }).click();
      await page.locator('.settings-topbar').getByRole('button', { name: '返回', exact: true }).click();
      assert.equal(await page.locator('.composer textarea').inputValue(), 'Draft survives settings');
      if (!mobile) assert.equal(await page.locator('.sidebar').isVisible(), true, 'return restores the sidebar preference');
      await page.waitForFunction(top => Math.abs(document.querySelector('.scroller').scrollTop - top) < 2, scroll).catch(async () => {
        assert.fail(JSON.stringify({ mobile, theme, expectedScroll: scroll, actual: await page.locator('.scroller').evaluate(el => ({ top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight })) }));
      });
      assert.deepEqual(errors, []);
      await context.close();
    }
  } finally { await browser.close(); await daemon.stop(); }
});

test('the back swipe and the back control share one trail, and no old page is left behind', async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: 'Swipe return' }) })).json();
    await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'Edge swipe target' }) });
    for (const mobile of [true, false]) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile });
      const page = await context.newPage(); page.setDefaultTimeout(10000);
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await openUi(page, daemon);
      const where = mobile ? 'phone' : 'desktop';
      const openList = () => page.getByRole('button', { name: '打开线程列表', exact: true }).click();
      const openThread = async () => {
        if (mobile) await openList();
        await page.locator('.sidebar .thread-item', { hasText: 'Swipe return' }).click();
        await page.locator('.composer textarea').waitFor();
      };
      const openSettings = async () => {
        if (mobile) await openList();
        await page.locator('.sidebar').getByRole('button', { name: '配置', exact: true }).click();
        await page.locator('.settings-page').waitFor();
      };
      const appearance = async () => {
        await page.locator('.settings-nav').getByRole('button', { name: '外观', exact: true }).click();
        await page.waitForFunction(() => location.hash === '#/settings/appearance');
      };
      const length = () => page.evaluate(() => history.length);
      // Whatever came before, a back swipe must not have brought a settings page (or any other) back.
      const inConversation = async () => {
        await sleep(300); // a re-entered settings page would land with a hashchange by then
        assert.equal(await page.locator('.settings-page').count(), 0, `a settings page came back (${where})`);
        assert.equal(await page.evaluate(() => location.hash), '', `address left the conversation (${where})`);
        await page.locator('.composer textarea').waitFor({ state: 'attached', timeout: 2000 });
      };
      const listOpen = () => page.locator('.drawer-open').count();
      // Settings opened from the phone's list go back to the list, whichever back was used; the
      // next back closes it like any other.
      const backFromSettings = async () => {
        await inConversation();
        if (!mobile) return;
        assert.equal(await listOpen(), 1, 'closing settings opened from the list did not go back to the list');
        await page.goBack(); await sleep(300);
        assert.equal(await listOpen(), 0, 'a back swipe on the list did not close it');
      };

      await openThread();
      const floor = await length();

      // Swipe first, then the control: both go up the same trail, neither lands on a stale page.
      await openSettings(); await appearance();
      await page.goBack(); // to the settings index
      await page.waitForFunction(() => location.hash === '#/settings');
      await page.locator('.settings-page').waitFor();
      await closeSettings(page); await backFromSettings();
      assert.equal(await length(), floor, `opening and closing settings added history (${where})`);

      // The control first, then the swipe.
      await openSettings(); await appearance();
      if (mobile) {
        await page.getByRole('button', { name: '返回', exact: true }).click(); // detail to index
        await page.waitForFunction(() => location.hash === '#/settings');
        await page.goBack(); // leaves settings
        await backFromSettings();
      } else {
        await page.goBack(); await page.waitForFunction(() => location.hash === '#/settings');
        // The app's entry goes back on top a frame or two after a back lands on the floor.
        await page.waitForFunction(() => history.state?.rahApp);
        await page.goBack(); await inConversation();
      }
      assert.equal(await length(), floor, `a swipe added history (${where})`);

      // Many rounds leave nothing behind for a swipe to land on.
      for (let i = 0; i < 3; i++) { await openSettings(); await appearance(); await closeSettings(page); await backFromSettings(); }
      assert.equal(await length(), floor, `rounds of settings piled up history (${where})`);

      // A page reloaded on settings and then closed has no old settings entry below it.
      await openSettings(); await page.reload(); await page.locator('.settings-page').waitFor();
      await closeSettings(page); await inConversation();
      if (mobile) for (let i = 0; i < 4; i++) { await page.goBack(); await sleep(300); assert.equal(await page.locator('.settings-page').count(), 0, 'a swipe after a reload landed on settings'); }
      else await inConversation();

      if (mobile) {
        // On the conversation a back swipe is the top-left control: it opens the list, and the next closes it.
        await page.evaluate(() => { location.hash = ''; });
        await sleep(200);
        const before = await listOpen();
        await page.goBack(); await sleep(300);
        assert.notEqual(await listOpen(), before, 'a back swipe did not move the list');
        await page.goBack(); await sleep(300);
        assert.equal(await listOpen(), before, 'the next back swipe did not undo it');
        assert.equal(await page.locator('.settings-page').count(), 0);

        // iOS fires the history back for the same finger the app's own edge swipe already used: one gesture, one move.
        const touch = await context.newCDPSession(page);
        await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: 6, y: 300 }] });
        await touch.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: 160, y: 300 }] });
        await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
        await page.goBack(); await sleep(300);
        assert.equal(await listOpen(), 1, 'one edge swipe opened the list and then closed it again');
        await touch.detach();
        await sleep(900); // a later swipe is a gesture of its own
        await page.goBack(); await sleep(300);
        assert.equal(await listOpen(), 0);
      }

      // A followed settings link is an ordinary entry; closing it must not leave settings behind.
      await page.evaluate(hash => { location.hash = hash; }, '#/settings/appearance');
      await page.locator('.settings-page').waitFor();
      await closeSettings(page);
      await page.waitForFunction(() => !document.querySelector('.settings-page'));
      await page.waitForFunction(() => location.hash === '');
      await sleep(300);
      assert.equal(await listOpen(), 0, 'settings not opened from the list came back with the list open');

      // Leaving the schedules page through the sidebar leaves no trail either.
      if (mobile) await openList();
      if (await page.getByRole('button', { name: '定时任务', exact: true }).count()) {
        await page.getByRole('button', { name: '定时任务', exact: true }).click();
      } else {
        await page.locator('.side-more').click();
        await page.getByRole('menuitem', { name: '定时任务', exact: true }).click();
      }
      await page.locator('.sched-page').waitFor();
      await page.waitForFunction(() => window.location.hash === '#/schedules');
      if (mobile) await openList();
      await page.locator('.sidebar .thread-item', { hasText: 'Swipe return' }).click();
      await page.locator('.composer textarea').waitFor();
      await page.waitForFunction(() => window.location.hash === '');
      if (mobile) { await page.goBack(); await sleep(300); assert.equal(await page.locator('.sched-page').count(), 0, 'a swipe landed on the closed schedules page'); }

      assert.deepEqual(errors, []);
      await context.close();
    }
  } finally { await browser.close(); await daemon.stop(); }
});
