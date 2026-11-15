import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { startDaemon, launchBrowser, openUi, api } from '../harness.mjs';

test('glass chrome has scrolling content behind it without hiding either end of the conversation', { timeout: 90000 }, async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  const shots = '/tmp/calli-glass-surfaces'; mkdirSync(shots, { recursive: true });
  try {
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: '玻璃材质与页面导航' }) })).json();
    await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: Array.from({ length: 35 }, (_, i) => `### ${i + 1}. 统一菜单与配置体验\n\n配置采用完整页面，电脑分类与详情并排，手机逐层进入。滚动时观察顶部按钮与输入框后的内容。`).join('\n\n') }) });
    for (let attempt = 0; ; attempt++) {
      const current = await (await api(daemon, `/api/threads/${thread.id}`)).json();
      if (current.status === 'idle') break;
      assert.ok(attempt < 100, 'the fixture answer finishes before testing manual scroll positions');
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    for (const mobile of [false, true]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, colorScheme: theme });
      if (mobile) await context.addInitScript(mode => localStorage.setItem('calli.appearance', JSON.stringify({ mode, palette: 'graphite' })), theme);
      const page = await context.newPage(); page.setDefaultTimeout(10000);
      const cdp = await context.newCDPSession(page);
      if (mobile) await cdp.send('Emulation.setSafeAreaInsetsOverride', { insets: { top: 59, right: 0, bottom: 34, left: 0 } });
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      await openUi(page, daemon);
      await page.addStyleTag({ content: '*,*::before,*::after{transition:none!important;animation:none!important}' });
      if (mobile) await page.getByRole('button', { name: '打开线程列表', exact: true }).click();
      await page.locator('.sidebar .thread-item', { hasText: thread.title }).click();
      await page.locator('.thread .markdown').first().waitFor();
      const scroller = page.locator('.main > .scroller');
      await page.waitForFunction(() => Number.parseFloat(document.querySelector('.main').style.getPropertyValue('--chat-bottom-overlap')) > 0);
      const measure = () => page.evaluate(() => {
        const rect = selector => { const r = document.querySelector(selector).getBoundingClientRect(); return { top: r.top, bottom: r.bottom, height: r.height }; };
        const slot = document.querySelector('.composer-slot');
        const blur = slot.getBoundingClientRect().bottom - Number.parseFloat(getComputedStyle(slot, '::before').height);
        return { scroller: rect('.main > .scroller'), top: rect('.topbar'), input: rect('.composer-box'), thread: rect('.thread'), blur };
      });
      let bounds = await measure();
      assert.ok(bounds.scroller.top <= bounds.top.top + 1, 'content scrolls behind the top controls');
      if (mobile) {
        assert.ok(bounds.scroller.bottom >= bounds.input.bottom, 'content scrolls behind the input');
      } else {
        // A wide screen keeps the glass top bar but draws the composer as an opaque card,
        // with the conversation ending above it.
        const solid = await page.evaluate(() => {
          const top = document.querySelector('.main > .topbar');
          const pill = document.querySelector('.topbar-actions');
          const box = document.querySelector('.composer-box');
          const cs = (el, pseudo) => getComputedStyle(el, pseudo);
          return {
            band: [cs(top).borderBottomWidth, cs(top, '::before').display, cs(top, '::after').display].join('|'),
            pill: cs(pill).backdropFilter,
            box: [cs(box).backgroundColor, cs(box).backdropFilter].join('|'),
          };
        });
        assert.equal(solid.band, '0px|block|block', 'the top bar keeps its fade bands and draws no hairline');
        assert.notEqual(solid.pill, 'none', 'the top bar buttons keep their glass capsule');
        assert.notEqual(solid.box.split('|')[0], 'rgba(0, 0, 0, 0)', 'the composer card has a surface of its own');
        assert.equal(solid.box.split('|')[1], 'none', 'the composer card is opaque');
        assert.ok(bounds.scroller.bottom <= bounds.input.top + 1, 'a wide screen ends the conversation above the composer');
      }
      await scroller.evaluate(el => { el.scrollTop = 0; });
      bounds = await measure();
      assert.ok(bounds.thread.top >= bounds.top.bottom, 'the first message starts below the header');
      await scroller.evaluate(el => { el.scrollTop = el.scrollHeight; });
      bounds = await measure();
      assert.ok(bounds.thread.bottom <= bounds.input.top - 10, 'the last message remains above the input');
      if (mobile) assert.ok(bounds.thread.bottom <= bounds.blur, 'at rest the newest output stays clear of the blur under the composer');
      await page.locator('.composer textarea').fill('多行输入\n检查输入框增高\n仍保留最后一条消息\n以及底部操作');
      await page.waitForTimeout(100);
      await scroller.evaluate(el => { el.scrollTop = el.scrollHeight; });
      bounds = await measure();
      assert.ok(bounds.thread.bottom <= bounds.input.top - 10, 'composer growth updates the reading clearance');
      await page.locator('.composer textarea').fill('');
      await page.locator('.composer textarea').blur();
      await page.waitForTimeout(150); // the touch composer settles into its collapsed height
      await scroller.evaluate(el => { el.scrollTop = 600; });
      if (mobile) {
        // Stress the seam with a contrasting backdrop: both the status area and the first
        // pixel below it must stay the same colour, even when dark content passes behind.
        // Some iOS modes keep the system bar outside the web viewport and report a zero inset.
        for (const inset of [0, 59]) {
          await cdp.send('Emulation.setSafeAreaInsetsOverride', { insets: { top: inset, right: 0, bottom: 34, left: 0 } });
          await page.evaluate(() => {
            const probe = document.createElement('div'); probe.id = 'status-seam-probe';
            Object.assign(probe.style, { position: 'absolute', inset: '0 0 auto', height: '140px', background: 'var(--bg)', pointerEvents: 'none' });
            document.querySelector('.main').append(probe);
          });
          const pixel = y => page.screenshot({ clip: { x: 1, y, width: 1, height: 1 } });
          const plain = await pixel(1);
          await page.evaluate(() => { document.querySelector('#status-seam-probe').style.background = 'var(--text)'; });
          assert.ok(plain.equals(await pixel(1)), 'the top edge matches the system status colour over contrasting content');
          assert.ok(plain.equals(await pixel(inset + 5)), 'the safe area and navigation join without a colour seam');
          await page.evaluate(() => document.querySelector('#status-seam-probe').remove());
        }
        const top = await page.locator('.topbar').boundingBox();
        const back = await page.locator('.topbar .menu-btn').boundingBox();
        assert.ok(back.y >= top.y + 59, 'controls stay below the status-bar safe area');
      }
      await page.screenshot({ path: `${shots}/chat-${mobile ? 'phone' : 'desktop'}-${theme}.png` });
      await page.locator('.topbar-more').click();
      await page.getByRole('menuitem', { name: '复制', exact: true }).waitFor();
      await page.screenshot({ path: `${shots}/menu-${mobile ? 'phone' : 'desktop'}-${theme}.png` });
      await page.keyboard.press('Escape');
      // Escape is a keypress, so focus returns to the button with :focus-visible matched — the
      // moment a ring can look like a stray box. Round buttons hug their ring instead.
      const ring = await page.locator('.topbar-more').evaluate(el => ({ offset: getComputedStyle(el).outlineOffset, style: getComputedStyle(el).outlineStyle }));
      assert.equal(ring.style, 'solid');
      assert.equal(ring.offset, '-2px', 'the top bar buttons hug their focus ring instead of wearing a stray box');
      await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-transparency', value: 'reduce' }] });
      assert.equal(await page.locator('.composer-box').evaluate(el => getComputedStyle(el).backdropFilter), 'none', 'reduced transparency keeps the opaque fallback');
      await cdp.detach();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
      assert.deepEqual(errors, []);
      await context.close();
    }
  } finally { await browser.close(); await daemon.stop(); }
});

test('focusing the composer keeps its glass edge, and floating buttons share the material', { timeout: 60000 }, async () => {
  const daemon = await startDaemon();
  const browser = await launchBrowser();
  try {
    const thread = await (await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: '输入框边缘' }) })).json();
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    const page = await context.newPage(); page.setDefaultTimeout(10000);
    await openUi(page, daemon);
    await page.addStyleTag({ content: '*,*::before,*::after{transition:none!important;animation:none!important}' });
    const edge = () => page.locator('.composer-box').evaluate(el => { const s = getComputedStyle(el); return { shadow: s.boxShadow, border: s.borderTopColor }; });
    const sidebar = await page.locator('.round-btn').first().evaluate(el => getComputedStyle(el).backdropFilter);
    assert.notEqual(sidebar, 'none', 'the sidebar round buttons are glass');
    await page.getByRole('button', { name: '打开线程列表', exact: true }).click();
    await page.locator('.sidebar .thread-item', { hasText: thread.title }).click();
    await page.locator('.composer-box').waitFor();
    const resting = await edge();
    await page.locator('.composer textarea').click();
    assert.deepEqual(await edge(), resting, 'focus adds no darker ring around the composer');
    assert.equal(await page.locator('.menu-dot').count(), 0, 'the list button carries no dot');
    await context.close();
  } finally {
    await browser.close();
    await daemon.stop();
  }
});

test('the phone edges wash only where text scrolls out, and the read-only handover note is a small glass chip', { timeout: 90000 }, async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-glass-chain-'));
  const daemon = await startDaemon({ CALLI_AGENTS_JSON: JSON.stringify({ agents: ['first', 'next'].map((id, i) => ({ id, adapter: 'claude', label: `Agent ${i + 1}`, command: ['fake-claude'], env: { CLAUDE_CONFIG_DIR: join(dir, id), FAKE_SESSION_ROOT: join(dir, id) } })) }) });
  const browser = await launchBrowser();
  try {
    const post = async (path, body) => { const response = await api(daemon, path, { method: 'POST', body: JSON.stringify(body) }); if (!response.ok) assert.fail(await response.text()); return response.json(); };
    const source = await post('/api/threads', { agent: 'first', cwd: daemon.workdir, title: '玻璃边缘交接' });
    await post(`/api/threads/${source.id}/turns`, { text: '先说一句' });
    for (let attempt = 0; ; attempt++) {
      if ((await (await api(daemon, `/api/threads/${source.id}`)).json()).status === 'idle') break;
      assert.ok(attempt < 100, 'the fixture answer finishes'); await new Promise(resolve => setTimeout(resolve, 100));
    }
    await post(`/api/threads/${source.id}/handover-thread`, { agent: 'next', mode: 'continue' });
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    const page = await context.newPage(); page.setDefaultTimeout(10000);
    const cdp = await context.newCDPSession(page);
    await cdp.send('Emulation.setSafeAreaInsetsOverride', { insets: { top: 59, right: 0, bottom: 34, left: 0 } });
    await openUi(page, daemon);
    await page.addStyleTag({ content: '*,*::before,*::after{transition:none!important;animation:none!important}' });
    await page.getByRole('button', { name: '打开线程列表', exact: true }).click();
    await page.locator('.handover-group > .thread-row .thread-item').click();
    await page.locator('.composer-box').waitFor();
    const edges = () => page.evaluate(() => {
      const px = value => Number.parseFloat(value);
      const slot = document.querySelector('.composer-slot'), composer = slot.querySelector(':scope > .composer');
      const wash = getComputedStyle(slot, '::after'), blur = getComputedStyle(slot, '::before');
      const bar = document.querySelector('.main > .topbar'), barWash = getComputedStyle(bar, '::after');
      return {
        slot: slot.getBoundingClientRect().height, composer: composer.getBoundingClientRect().height,
        washHeight: px(wash.height), washBottom: px(wash.bottom), washTint: wash.backgroundImage, blurFilter: blur.backdropFilter,
        bar: bar.getBoundingClientRect().height, barWashHeight: px(barWash.height),
      };
    });
    const plain = await edges();
    assert.equal(plain.washBottom, 0, 'the bottom wash runs down to the screen edge');
    assert.ok(Math.abs(plain.washHeight - (plain.composer + 40)) <= 1, 'the bottom wash is the composer plus 40px of fade above its box');
    assert.ok(!/rgba\(0, 0, 0, 0\.\d*[1-9]/.test(plain.washTint), 'the bottom wash is the page colour, not a grey scrim');
    assert.notEqual(plain.blurFilter, 'none', 'the fade above the composer is blurred');
    assert.ok(Math.abs(plain.barWashHeight - (plain.bar + 30)) <= 1, 'the top wash eases out 30px below the bar');
    // Open the earlier segment: its note joins the slot, but the wash still follows the composer alone.
    await page.getByLabel('这个线程的更多操作').click();
    await page.getByRole('menuitem', { name: '分叉与交接' }).click();
    await page.getByRole('menuitem', { name: '交接记录', exact: true }).click();
    await page.getByRole('dialog', { name: '交接记录', exact: true }).waitFor();
    await page.getByRole('menuitem').filter({ hasText: '原始会话' }).click();
    await page.locator('.handover-readonly').waitFor();
    const note = await page.locator('.handover-readonly').evaluate(el => {
      const box = el.getBoundingClientRect(), style = getComputedStyle(el), composer = document.querySelector('.composer-slot > .composer').getBoundingClientRect();
      return { width: box.width, height: box.height, bottom: box.bottom, gap: composer.top - box.bottom, filter: style.backdropFilter, z: style.zIndex, radius: style.borderTopLeftRadius };
    });
    assert.notEqual(note.filter, 'none', 'the note is a glass chip, not bare text over the thread');
    assert.equal(note.z, '1', 'the chip sits above the wash like the rest of the slot, and the box keeps its menus on top of it');
    assert.ok(note.height < 90 && note.gap >= 4 && note.gap <= 16, 'a small chip just above the box, not a block');
    assert.ok(note.width <= 390 - 32, 'the chip stays inside the page margins');
    const withNote = await edges();
    assert.ok(withNote.slot > withNote.composer + 60, 'the slot now holds the note as well as the box');
    assert.ok(Math.abs(withNote.washHeight - (withNote.composer + 40)) <= 1, 'the wash does not grow with the note stacked above the box');
    mkdirSync('/tmp/calli-glass-surfaces', { recursive: true });
    await page.screenshot({ path: '/tmp/calli-glass-surfaces/handover-phone.png' });
    await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-transparency', value: 'reduce' }] });
    assert.equal(await page.locator('.handover-readonly').evaluate(el => getComputedStyle(el).backdropFilter), 'none', 'reduced transparency draws the chip solid');
    await context.close();
  } finally { await browser.close(); await daemon.stop(); }
});
