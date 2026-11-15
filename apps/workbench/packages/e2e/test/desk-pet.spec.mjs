import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { launchBrowser } from '../harness.mjs';

test('a pet reply naming a child opens its parent pile, and an unconfigured delegate opens settings', { timeout: 30_000 }, async () => {
  // Check the Desk/Pet contract independently of the Router transport and sprite installation. The pet
  // sits above every page now, so the two are wired the way App wires them: her reply names a matter,
  // the desk opens the pile it belongs to, and the pile owns the keyboard while it is spread out.
  const bundle = await build({
    stdin: { contents: `
      import { createRoot } from 'react-dom/client';
      import { useState } from 'react';
      import { DeskPage } from './components/DeskPage';
      import { Pet } from './components/Pet';
      const createdAt = new Date().toISOString();
      const base = { done:false, position:0, createdAt, doneAt:null, threads:[] };
      const todos = [
        { ...base, id:'parent', text:'Review experiment', parentId:null, lane:'later' },
        { ...base, id:'child', text:'Check control group', parentId:'parent' },
      ];
      const api = {
        pet: async () => ({installed:true, installing:false, error:null, id:'dafeiyu', commit:'test', clips:{idle:1}, frameMs:84}),
        petFrame: () => 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
        petRouter: async () => ({threadId:'router', status:'idle', lastReply:'Next: Check control group', lastReplyAt:createdAt}),
        todoNote: async () => ({text:'',updatedAt:null,threadId:null,rev:0}),
        workdirs: async () => ({workdirs:[]}),
        personalProfile: async () => ({text:'', updatedAt:null, path:'profile.md', isTemplate:false, rev:1}),
        lastPersonalBrief: async () => null,
        pushStatus: async () => ({records:[]}),
        briefSettings: async () => ({enabled:false, morning:'09:00', evening:'19:00'}),
        proactiveSettings: async () => ({settings:{mode:'off', repos:[], enabled:false}}),
        pushSettings: async () => ({enabled:false}),
        larkChannel: async () => ({enabled:false, people:[], chats:[], intervalMin:30}),
        larkStatus: async () => ({cli:false, loggedIn:false, lastRunAt:null, nextRunAt:null, lastError:null, lastSignals:0}),
      };
      localStorage.setItem('calli.pet.pending', JSON.stringify({since:null,threadId:'router',sentAt:Date.now(),text:'What next?'}));
      function TestApp() {
        const [revealTodoId, setRevealTodoId] = useState(null);
        const [spread, setSpread] = useState(false);
        return <>
          <DeskPage api={api} todos={todos} loading={false} error="" threads={[]} agents={[]}
            personal={{agent:null,model:null,directory:null}} personalError="" refreshKey={0} onReload={() => {}}
            onPersonalChange={() => {}} onPersonalRetry={() => {}} onOpenThread={() => {}} onOpenPlans={() => {}}
            onSpreadChange={setSpread} revealTodoId={revealTodoId} onRevealTodo={() => setRevealTodoId(null)} />
          <Pet api={api} greeting={null} spreadOpen={spread} desk={true} place="corner" threads={[]}
            onOpenThread={() => {}} todos={todos.filter(todo => !todo.done)} onOpenTodo={id => setRevealTodoId(id)} />
        </>;
      }
      createRoot(document.getElementById('root')).render(<TestApp />);
    `, loader:'tsx', resolveDir:fileURLToPath(new URL('../../web/src/', import.meta.url)) },
    bundle:true, write:false, format:'iife', platform:'browser', jsx:'automatic',
  });
  const css = readFileSync(new URL('../../web/src/styles.css', import.meta.url), 'utf8');
  const server = createServer((_req, res) => res.end('<!doctype html><div id="root"></div>'));
  let browser;
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    browser = await launchBrowser();
    const page = await browser.newPage({ reducedMotion:'reduce', locale:'zh-CN' });
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.addStyleTag({ content:css });
    await page.addScriptTag({ content:bundle.outputFiles[0].text });
    await page.locator('.desk-week.quiet').waitFor();
    assert.ok((await page.locator('.desk-week').boundingBox()).height < 120, 'zero completions use a compact, explicit empty week');
    assert.equal(await page.locator('.desk-week .desk-chart-day').count(), 7);
    // Her reply names a matter: the word in the bubble spreads that pile on the desk below.
    await page.locator('.pet-bub', { hasText:'Next' }).waitFor();
    await page.locator('.pet-bub-act', { hasText:'Check control group' }).click();
    await page.locator('#desk-spread-title', { hasText:'Review experiment' }).waitFor();
    assert.equal(await page.locator('.pet-bub').count(), 0, 'opening the matter clears the reply bubble');
    await page.getByRole('checkbox', { name:'Check control group' }).waitFor();
    // While the pile is spread she steps back, and a tap talks to her in one thin line.
    assert.equal(await page.locator('.pet-back').count(), 1);
    await page.locator('.pet-body').click();
    await page.locator('.pet-qc input').waitFor();
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => Boolean(document.activeElement.closest('.pet-qc'))), false, 'the spread takes keyboard focus from another dialog');
    await page.locator('.pet-qc input').focus();
    await page.keyboard.press('Escape');
    await page.locator('.pet-qc').waitFor({ state:'detached' });
    assert.equal(await page.locator('.desk-spread').count(), 1);
    const delegate = page.getByRole('button', { name:'交给 agent', exact:true });
    assert.equal(await delegate.isEnabled(), true);
    assert.equal(await delegate.getAttribute('title'), '先在私人设置里选 agent');
    await delegate.click();
    await page.locator('.personal-settings').waitFor();
    await page.keyboard.press('Escape');
    await page.locator('.personal-settings').waitFor({ state:'detached' });
    assert.equal(await page.locator('.desk-spread').count(), 1);
    await page.keyboard.press('Escape');
    await page.locator('.desk-spread').waitFor({ state:'detached' });
    await page.waitForFunction(() => document.activeElement.id === 'desk-stack-later');
    assert.deepEqual(errors, []);
  } finally {
    await browser?.close();
    server.close();
  }
});

test('the pet clears a growing composer and phone switcher, preserves dragged positions, and only renders a tucked avatar', { timeout: 30_000 }, async () => {
  const bundle = await build({
    stdin: { contents: `
      import { createRoot } from 'react-dom/client';
      import { useState } from 'react';
      import { Pet, savePetPresence } from './components/Pet';
      const api = {
        pet: async () => ({installed:true, id:'test', commit:'test', clips:{idle:1}, frameMs:84}),
        petFrame: () => 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
        personalProfile: async () => ({isTemplate:false}),
      };
      function App() {
        const [text, setText] = useState('');
        const [attached, setAttached] = useState(false);
        return <>
          <button id="presence" onClick={() => savePetPresence('event')}>Tuck</button>
          <div className="quick-switch"><button className="quick-switch-button">Threads</button></div>
          <div className="composer-slot" style={{position:'fixed', bottom:0, right:0, width:'75%', padding:12}}>
            {attached && <div style={{height:80}}>Attachment</div>}
            <textarea aria-label="Message" value={text} onChange={e=>setText(e.target.value)} style={{height:40 + 20 * text.split('\\n').length, width:'100%'}} />
            <button onClick={()=>setAttached(was=>!was)}>Attach</button>
          </div>
          <Pet api={api} greeting={null} spreadOpen={false} desk={false} place="thread" threads={[]} onOpenThread={()=>{}} />
        </>;
      }
      createRoot(document.getElementById('root')).render(<App />);
    `, loader:'tsx', resolveDir:fileURLToPath(new URL('../../web/src/', import.meta.url)) },
    bundle:true, write:false, format:'iife', platform:'browser', jsx:'automatic',
  });
  const css = readFileSync(new URL('../../web/src/styles.css', import.meta.url), 'utf8');
  const server = createServer((_req, res) => res.end('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><div id="root"></div>'));
  let browser;
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    browser = await launchBrowser();
    for (const phone of [false, true]) {
      const page = await browser.newPage({ viewport:phone ? {width:390,height:844} : {width:1280,height:800}, hasTouch:phone, isMobile:phone, reducedMotion:'reduce' });
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      await page.goto(`http://127.0.0.1:${server.address().port}`);
      await page.addStyleTag({ content:css });
      await page.addScriptTag({ content:bundle.outputFiles[0].text });
      await page.locator('.pet-body').waitFor();
      const clear = async () => {
        await page.waitForFunction(() => {
          const composer = document.querySelector('.composer-slot').getBoundingClientRect();
          return [...document.querySelectorAll('.pet-body, .pet-under')].every(el => el.getBoundingClientRect().bottom <= composer.top - 11.9);
        });
      };
      if (phone) await page.tap('.pet-body'); else await page.hover('.pet-body');
      await clear();
      assert.equal(await page.locator('.pet-avatar').count(), 0);
      await page.getByLabel('Message').fill('one\ntwo\nthree\nfour');
      await page.getByRole('button', {name:'Attach',exact:true}).click();
      await clear();
      if (phone) {
        const bounds = await page.evaluate(() => ['.pet-body canvas','.quick-switch-button'].map(s => document.querySelector(s).getBoundingClientRect().toJSON()));
        assert.ok(bounds[0].bottom + 12 <= bounds[1].top || bounds[1].bottom + 12 <= bounds[0].top, 'the quick switch circle stays separate from her body');
      } else {
        const body = await page.locator('.pet-body').boundingBox();
        await page.mouse.move(body.x + 20, body.y + 20);
        await page.mouse.down();
        await page.mouse.move(80, 160, {steps:8});
        await page.mouse.up();
        const saved = await page.evaluate(() => localStorage.getItem('calli.pet.pos'));
        assert.ok(saved);
        const before = await page.locator('.pet-body').boundingBox();
        await page.getByRole('button', {name:'Attach',exact:true}).click();
        const after = await page.locator('.pet-body').boundingBox();
        assert.equal(after.x, before.x);
        assert.equal(after.y, before.y, 'a safe dragged position is respected when the composer shrinks');
        await page.evaluate(() => localStorage.setItem('calli.pet.pos', JSON.stringify({left:1100,lift:12})));
        await page.reload();
        await page.addStyleTag({content:css});
        await page.addScriptTag({content:bundle.outputFiles[0].text});
        await page.locator('.pet-body').waitFor();
        await clear();
        assert.equal(await page.locator('.pet-body').evaluate(el=>el.getBoundingClientRect().left), 1100, 'a saved collision moves up without discarding the chosen horizontal position');
      }
      await page.locator('#presence').click();
      await page.locator('.pet-avatar .pet-bust').waitFor();
      await page.locator('.pet-avatar').click();
      assert.equal(await page.locator('.pet-avatar').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally {
    await browser?.close();
    server.close();
  }
});
