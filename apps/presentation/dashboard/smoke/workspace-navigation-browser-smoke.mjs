// Packaged workspace navigation with synthetic source/host descriptors.
// No model, source mutation, setting apply or session start.
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {startServer, port, outputDir, packaged} from '../../../../examples/personal-workspace-browser/fixture.mjs';
import {launchBrowser, loadPlaywright, waitForHttp} from '../../../../examples/dashboard-browser-smoke-support.mjs';
import {openWorkspacePage} from '../../../../examples/personal-workspace-browser/scenario-context.mjs';
import {installShowcaseWorkspace} from '../../../../examples/personal-workspace-browser/showcase-workspace.mjs';

const baseline = process.env.LOOPX_NAVIGATION_CAPTURE_BASE === '1';
const stage = baseline ? 'before' : 'after';
const server = await startServer();
let browser;
const url = `http://127.0.0.1:${port}/${packaged ? 'chat/' : ''}?statusUrl=/status.json`;
const dialog = page => page.locator('dialog.workspace-command-palette');
try {
  await waitForHttp(url); await mkdir(outputDir, {recursive:true});
  browser = await launchBrowser(loadPlaywright().chromium);
  for (const locale of ['zh-CN', 'en']) {
    const session = await openWorkspacePage({newPage: options => browser.newPage({...options, hasTouch:true})}, url, {beforeGoto: async (api, page) => {
      await page.addInitScript(value => localStorage.setItem('loopx-pw-locale', value), locale);
      await installShowcaseWorkspace(api, page, {done:23, cost:24.8});
      const transform = api.transformStatus;
      api.transformStatus = data => {
        const result = transform(data) ?? data;
        const goals = result.run_history.goals, original = goals.find(goal => goal.id === 'progress-projection');
        goals.push({...structuredClone(original), id:'readonly-archive', display_name:'Graph · archived experiment',
          source_access:{namespace:'a'.repeat(64),label:'Graph · archived experiment',read_only:true}});
        for (let i=0; i<24; i++) goals.push({...structuredClone(original), id:`navigation-goal-${i}`,
          display_name:`${i+1}. 核验长程结果与完整来源 Verify the complete export history`});
        return result;
      };
    }});
    const {page} = session;
    try {
      await page.locator('.personal-goal-link', {hasText:'Graph · archived experiment'}).click();
      await page.getByRole('heading', {name:'Graph · archived experiment', exact:true}).waitFor();
      const launch = page.getByRole('button', {name:locale==='en'?'Quick navigation':'快速导航',exact:true});
      const settings = page.getByRole('button', {name:locale==='en'?'Settings':'设置',exact:true});
      const writes=[]; page.on('request', r => {if (!['GET','HEAD','OPTIONS'].includes(r.method())) writes.push(new URL(r.url()).pathname);});
      for (const [name,width,height] of [['desktop',1440,900],['low',1280,540],['narrow',390,640]]) {
        await page.setViewportSize({width,height});
        if (width===390) await page.locator('.personal-mobile-menu').click();
        await page.locator('.personal-sidebar-nav').evaluate(e => {e.scrollTop=0;});
        await page.mouse.move(width-3,3);
        await page.screenshot({path:resolve(outputDir,`navigation-${stage}-${name}-${locale}.png`),animations:'disabled'});
        if (!baseline) {
          assert.equal(await settings.isVisible(),true);
          const bounds=await settings.boundingBox(); assert.ok(bounds.y>=0 && bounds.y+bounds.height<=height);
          const searchBounds=await launch.boundingBox(); assert.equal(searchBounds.width,44); assert.equal(searchBounds.height,44);
          assert.equal(await launch.innerText(),'','no visible replacement label');
          await page.locator('.personal-sidebar-nav').evaluate(e => {e.scrollTop=e.scrollHeight;});
          assert.deepEqual(await settings.boundingBox(),bounds,'scrolling a long directory leaves settings in place');
          await settings.click();
          const panel=page.locator('.personal-settings-page');
          await panel.waitFor();
          assert.equal(await panel.getByRole('radio',{name:locale==='en'?'This device':'本机设置',exact:true}).isChecked(),true);
          assert.equal(await panel.getByRole('combobox',{name:locale==='en'?'Target Goal':'目标 Goal',exact:true}).count(),0);
          await panel.getByRole('radio',{name:locale==='en'?'One Goal':'Goal 设置',exact:true}).check();
          await panel.getByRole('combobox',{name:locale==='en'?'Target Goal':'目标 Goal',exact:true}).selectOption('readonly-archive');
          await panel.locator('.personal-settings-unavailable').waitFor();
          assert.equal(await panel.locator('.personal-capability-apply').count(),0);
          await page.screenshot({path:resolve(outputDir,`navigation-${stage}-restriction-${name}-${locale}.png`),animations:'disabled'});
          await panel.locator('.personal-settings-back').click();
          await panel.waitFor({state:'hidden'});
        } else assert.equal(await settings.count(),0,'baseline reproduces host settings hidden by archived Goal');
        if (width===390 && await page.locator('.personal-workspace-shell.mobile-sidebar-open').count()) await page.keyboard.press('Escape');
      }
      if (baseline) continue;
      // Empty/partial snapshots keep their truth in contextual glyphs. Reading
      // them cannot expand a parent disclosure, grant access or start work.
      for (const [name,width,height] of [['desktop',1440,900],['narrow',390,844]]) {
        await page.setViewportSize({width,height});
        const now = page.locator('.goal-now');
        const workHelp = now.getByRole('button',{name:locale==='en'?'Work coverage unconfirmed':'工作覆盖未确认',exact:true});
        const acceptance = page.locator('.goal-observation-details');
        const acceptanceHelp = acceptance.getByRole('button',{name:locale==='en'?'Acceptance observations unavailable':'验收观察未获取',exact:true});
        await workHelp.waitFor();
        assert.doesNotMatch(await now.innerText(), /当前快照|coverage is incomplete|没有可列出/,'No duplicate scope or empty-list paragraphs in the default view');
        assert.equal(await acceptance.evaluate(e=>e.open),false);
        await page.mouse.move(width-2,2);
        await page.screenshot({path:resolve(outputDir,`glyph-default-${name}-${locale}.png`),animations:'disabled'});
        await workHelp.hover();
        const bubble=page.locator('.context-help-popover:popover-open');
        await bubble.waitFor();
        assert.match(await bubble.innerText(),locale==='en'?/coverage is incomplete/:/覆盖范围不完整/);
        assert.ok(await bubble.evaluate(e=>parseFloat(getComputedStyle(e).fontSize)>=14),'Optional help uses reading text');
        await page.keyboard.press('Escape'); await bubble.waitFor({state:'hidden'});
        await acceptanceHelp.focus(); await bubble.waitFor();
        assert.match(await bubble.innerText(),locale==='en'?/unknown/:/仍未知/,'Missing observations never become successful acceptance');
        await page.keyboard.press('Escape'); await bubble.waitFor({state:'hidden'});
        assert.equal(await acceptanceHelp.evaluate(e=>document.activeElement===e),true,'Escape keeps the help trigger focused');
        await acceptanceHelp.tap(); await bubble.waitFor();
        assert.equal(await acceptance.evaluate(e=>e.open),false,'Tapping help does not toggle the surrounding evidence disclosure');
        const bounds=await bubble.boundingBox();
        assert.ok(bounds.x>=0 && bounds.x+bounds.width<=width && bounds.y>=0 && bounds.y+bounds.height<=height,'Complete help fits desktop and phone');
        await page.screenshot({path:resolve(outputDir,`glyph-help-${name}-${locale}.png`),animations:'disabled'});
        await page.locator('.personal-channel-title').click(); await bubble.waitFor({state:'hidden'});
      }
      await page.setViewportSize({width:1440,height:900});
      await launch.focus();
      await page.evaluate(() => {
        window.navigationBackgroundClicks = 0;
        document.addEventListener('click', event => {if (!event.target.closest('dialog.workspace-command-palette')) window.navigationBackgroundClicks++;});
      });
      for (const side of ['left','right','top','bottom']) {
        await launch.click(); await dialog(page).waitFor();
        const count=await page.evaluate(()=>window.navigationBackgroundClicks);
        const b=await dialog(page).boundingBox();
        const point={left:[b.x-5,b.y+30],right:[b.x+b.width+5,b.y+30],top:[b.x+30,b.y-5],bottom:[b.x+30,b.y+b.height+5]}[side];
        await page.mouse.click(...point); await dialog(page).waitFor({state:'hidden'});
        assert.equal(await page.evaluate(()=>window.navigationBackgroundClicks),count,'dismissal never clicks the underlying workspace');
        assert.equal(await launch.evaluate(e=>e===document.activeElement),true);
      }
      await launch.click(); await dialog(page).waitFor();
      const b=await dialog(page).boundingBox();
      await page.mouse.click(b.x+4,b.y+4); assert.equal(await dialog(page).isVisible(),true,'internal whitespace stays open');
      await page.mouse.move(b.x+4,b.y+4); await page.mouse.down(); await page.mouse.move(b.x-5,b.y+30); await page.mouse.up();
      assert.equal(await dialog(page).isVisible(),true,'drag from inside does not activate backdrop');
      const heading=await dialog(page).getByRole('heading').boundingBox();
      await page.mouse.move(heading.x,heading.y+10); await page.mouse.down();
      await page.mouse.move(heading.x+heading.width,heading.y+10); await page.mouse.up();
      await dialog(page).getByRole('listbox').hover(); await page.mouse.wheel(0,180);
      assert.equal(await dialog(page).isVisible(),true,'text selection and list scrolling stay inside');
      const search=dialog(page).getByRole('combobox'); await search.fill('Graph');
      await search.dispatchEvent('compositionstart'); await search.dispatchEvent('keydown',{key:'Enter',keyCode:229,isComposing:true});
      assert.equal(await dialog(page).isVisible(),true); await search.dispatchEvent('compositionend');
      await search.press('Escape'); await dialog(page).waitFor({state:'hidden'});
      await launch.focus(); await page.keyboard.press('Enter'); await dialog(page).waitFor();
      await page.keyboard.press('Control+k'); await dialog(page).waitFor({state:'hidden'});
      // Touch uses the same completed outside press; the sidebar closes first.
      await page.setViewportSize({width:390,height:640});
      await page.locator('.personal-mobile-menu').click(); await launch.click(); await dialog(page).waitFor();
      const mobile=await dialog(page).boundingBox();
      await page.touchscreen.tap(mobile.x-5,mobile.y+20);
      await dialog(page).waitFor({state:'hidden'});
      assert.equal(await page.locator('.personal-mobile-menu').evaluate(e=>e===document.activeElement),true,'mobile dismissal returns to the visible menu');
      assert.deepEqual(writes,[],'discovery, dismissal and readonly settings never mutate or start work');
      assert.deepEqual(session.errors,[]);
    } finally {await session.close();}
  }
  console.log(baseline?'Baseline captures reproduce missing settings.':'Workspace navigation: long directory, host/Goal isolation, desktop/low/390, EN/ZH, backdrop and focus passed.');
} finally {await browser?.close(); server.kill();}
