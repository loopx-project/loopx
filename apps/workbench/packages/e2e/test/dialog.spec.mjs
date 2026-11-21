import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { launchBrowser } from '../harness.mjs';

test('dialogs preserve input composition, nested Escape and focus when an action becomes disabled', { timeout: 30_000 }, async () => {
  const bundle = await build({
    stdin: { contents: `
      import { useState } from 'react';
      import { createRoot } from 'react-dom/client';
      import { createPortal } from 'react-dom';
      import { Dialog, ChoiceDialog, DialogSurface, PanelDialog } from './components/Dialog';
      import { Popover } from './components/Popover';
      function App() {
        const [kind, setKind] = useState('');
        const [nested, setNested] = useState(false);
        const [busy, setBusy] = useState(false);
        const menu = <Popover label="Destinations" placement="below" trigger="Menu">{() => <button role="menuitem">Private</button>}</Popover>;
        return <><button id="opener" onClick={() => { setBusy(false); setKind('surface'); }}>Surface</button>
          <button onClick={() => setKind('dialog')}>Input dialog</button>
          <button onClick={() => setKind('panel')}>Panel</button>
          <button onClick={() => setKind('choice')}>Choice</button>
          {kind === 'dialog' && <Dialog title="Rename" input={{ initial:'draft',label:'Name' }} confirmLabel="Save" onConfirm={() => setKind('')} onCancel={() => setKind('')}>{menu}</Dialog>}
          {kind === 'panel' && <PanelDialog title="Details" onClose={() => setKind('')}><input aria-label="Filter" /></PanelDialog>}
          {kind === 'choice' && <ChoiceDialog title="Choose" choices={[{id:'one',icon:null,label:'One',hint:''}]} onPick={() => setKind('')} onCancel={() => setKind('')} />}
          {kind === 'surface' && <DialogSurface labelledBy="surface-title" onClose={() => setKind('')}><h2 id="surface-title">Surface</h2>
            <textarea aria-label="Draft" />{menu}
            <button onClick={() => setNested(true)}>Nested dialog</button>
            <button id="last-action" disabled={busy} onClick={() => setBusy(true)}>Start request</button>
          </DialogSurface>}
          {nested && createPortal(<Dialog title="Nested" input={{initial:'child',label:'Child'}} confirmLabel="Save" onConfirm={() => setNested(false)} onCancel={() => setNested(false)} />, document.body)}
        </>;
      }
      createRoot(document.getElementById('root')).render(<App />);
    `, loader: 'tsx', resolveDir: fileURLToPath(new URL('../../web/src/', import.meta.url)) },
    bundle: true, write: false, format: 'iife', platform: 'browser', jsx: 'automatic',
  });
  const css = readFileSync(new URL('../../web/src/styles.css', import.meta.url), 'utf8');
  const server = createServer((_req, res) => res.end('<!doctype html><div id="root"></div>'));
  let browser;
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    browser = await launchBrowser();
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.addStyleTag({ content: css });
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    for (const name of ['Input dialog', 'Panel', 'Choice', 'Surface']) {
      await page.getByRole('button', { name, exact: true }).click();
      const dialog = page.getByRole('dialog');
      await dialog.waitFor();
      const field = dialog.locator('input,textarea');
      if (await field.count()) await field.first().fill('composing text');
      for (const ime of [{ isComposing: true }, { keyCode: 229 }]) {
        await page.evaluate(ime => document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key:'Escape',bubbles:true,...ime })), ime);
        assert.equal(await dialog.count(), 1, `${name}: composing Escape leaves the dialog open`);
      }
      await page.keyboard.press('Escape');
      await dialog.waitFor({ state: 'detached' });
    }
    await page.getByRole('button', { name: 'Input dialog', exact: true }).click();
    await page.getByRole('button', { name: 'Menu', exact: true }).click();
    await page.keyboard.press('Escape');
    assert.equal(await page.getByRole('dialog').count(), 1, 'a popover owns Escape above a regular dialog');
    await page.keyboard.press('Escape');
    await page.getByRole('dialog').waitFor({ state:'detached' });
    await page.locator('#opener').click();
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'last-action');
    await page.keyboard.press('Tab');
    assert.equal(await page.locator('textarea').evaluate(el => el === document.activeElement), true);
    await page.getByRole('button', { name: 'Menu', exact: true }).click();
    await page.keyboard.press('Escape');
    assert.equal(await page.getByRole('dialog').count(), 1);
    await page.getByRole('button', { name: 'Nested dialog', exact: true }).click();
    await page.getByRole('textbox', { name: 'Child', exact: true }).waitFor();
    await page.keyboard.press('Escape');
    await page.getByRole('dialog', { name: 'Nested', exact: true }).waitFor({ state:'detached' });
    assert.equal(await page.getByRole('dialog').count(), 1, 'only the top dialog closes');
    await page.getByRole('button', { name: 'Start request', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('#last-action').disabled);
    await page.keyboard.press('Escape');
    await page.getByRole('dialog').waitFor({ state:'detached' });
    assert.equal(await page.evaluate(() => document.activeElement.id), 'opener');
  } finally {
    await browser?.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});
