import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { launchBrowser } from '../../e2e/harness.mjs';

let vite, browser, base;
const shots = fileURLToPath(new URL('../../e2e/shots/mermaid-zoom/', import.meta.url));
before(async () => {
  vite = await createServer({ root: fileURLToPath(new URL('../', import.meta.url)), server: { port: 0, host: '127.0.0.1' } });
  await vite.listen();
  base = `http://127.0.0.1:${vite.httpServer.address().port}`;
  browser = await launchBrowser();
  mkdirSync(shots, { recursive: true });
});
after(async () => { await browser?.close(); await vite?.close(); });
async function setup(t, phone = false, theme = 'light', images = false) {
  const context = await browser.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 960 }, isMobile: phone, hasTouch: phone, colorScheme: theme });
  t.after(() => context.close());
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  await page.addInitScript(({ theme, images }) => { window.testImages = images; window.addEventListener('DOMContentLoaded', () => { document.documentElement.dataset.theme = theme; }, { once: true }); }, { theme, images });
  await page.route('**/src/main.tsx', route => route.fulfill({ contentType: 'text/javascript', body: "import '/e2e/mermaid-fixture.js';" }));
  await page.goto(base);
  await page.locator('.code-diagram svg').waitFor();
  return page;
}
async function view(page) {
  return page.locator('.lightbox-vector').evaluate(el => {
    const matrix = new DOMMatrix(getComputedStyle(el).transform);
    return { scale: matrix.a, x: matrix.e, y: matrix.f };
  });
}
async function open(page, keyboard = false) {
  const preview = page.locator('.code-diagram');
  if (keyboard) { await preview.focus(); await page.keyboard.press('Enter'); }
  else await preview.click();
  await page.locator('.lightbox-vector svg').waitFor();
  await page.locator('.lightbox').evaluate(el => Promise.all(el.getAnimations().map(animation => animation.finished)));
}

for (const phone of [false, true]) for (const theme of ['light', 'dark']) {
  test(`${phone ? 'phone' : 'desktop'} ${theme}: lazy vector view, fit/actual, zoom, pan and close`, async t => {
    const page = await setup(t, phone, theme);
    const prefix = `${phone ? 'phone' : 'desktop'}-${theme}`;
    assert.equal(await page.locator('.lightbox').count(), 0);
    await page.screenshot({ path: `${shots}${prefix}-preview.png` });
    await open(page, !phone);
    assert.equal((await view(page)).scale, 1);
    assert.equal(await page.locator('.lightbox img, .lightbox canvas').count(), 0);
    const geometry = await page.locator('.lightbox-vector').evaluate(el => ({ width: el.clientWidth, natural: el.querySelector('svg').viewBox.baseVal.width }));
    const styles = await page.evaluate(() => {
      const previews = ['.code-diagram', '.lightbox-vector'];
      return previews.map(s => { const node = document.querySelector(`${s} .node rect`); return getComputedStyle(node).fill; });
    });
    assert.equal(styles[0], styles[1], 'preview and enlarged SVG use the same theme');
    const ids = await page.locator('[id]').evaluateAll(nodes => nodes.map(n => n.id));
    assert.equal(new Set(ids).size, ids.length, 'enlarged SVG IDs are isolated');
    const missingMarkers = await page.locator('.lightbox-vector [marker-end]').evaluateAll(nodes => nodes.filter(node => {
      const id = node.getAttribute('marker-end').match(/#([^)]*)/)[1];
      return !node.closest('svg').querySelector(`[id="${id}"]`);
    }).length);
    assert.equal(missingMarkers, 0, 'arrow references belong to the enlarged SVG');
    await page.screenshot({ path: `${shots}${prefix}-fit.png` });
    await page.getByRole('button', { name: '按原始比例显示', exact: true }).click();
    assert.ok(Math.abs((await view(page)).scale - geometry.natural / geometry.width) < 0.01);
    const before = await view(page);
    await page.mouse.move(phone ? 200 : 720, 400);
    await page.mouse.down();
    await page.mouse.move(phone ? 280 : 900, 430, { steps: 8 });
    await page.mouse.up();
    assert.ok((await view(page)).x > before.x, 'drag pans horizontally');
    await page.screenshot({ path: `${shots}${prefix}-detail.png` });
    await page.getByRole('button', { name: '适应屏幕', exact: true }).click();
    assert.deepEqual(await view(page), { scale: 1, x: 0, y: 0 });
    await page.mouse.move(phone ? 195 : 720, 400);
    await page.mouse.wheel(0, -500);
    await page.waitForFunction(() => new DOMMatrix(getComputedStyle(document.querySelector('.lightbox-vector')).transform).a > 1);
    await page.getByRole('button', { name: '关闭', exact: true }).click();
    assert.equal(await page.locator('.lightbox').count(), 0);
    assert.equal(await page.locator('.code-diagram').evaluate(el => el === document.activeElement), true);
    await open(page, true);
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => !!document.activeElement.closest('.lightbox')), true);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('.lightbox').count(), 0);
  });
}

test('selection, source and copy do not open the viewer; theme changes update an open vector', async t => {
  const page = await setup(t);
  const block = page.locator('.code-block').first();
  await block.getByRole('button', { name: '代码', exact: true }).click();
  assert.equal(await page.locator('.lightbox').count(), 0);
  await block.getByRole('button', { name: '预览', exact: true }).click();
  await block.locator('.quiet-btn').click();
  assert.equal(await page.locator('.lightbox').count(), 0);
  await page.locator('.code-diagram').evaluate(el => {
    const range = document.createRange(); range.selectNodeContents(el.querySelector('.nodeLabel'));
    const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    el.click();
  });
  assert.equal(await page.locator('.lightbox').count(), 0);
  await page.evaluate(() => window.getSelection().removeAllRanges());
  await open(page);
  const oldId = await page.locator('.lightbox-vector svg').getAttribute('id');
  const oldFill = await page.locator('.lightbox-vector .node rect').first().evaluate(el => getComputedStyle(el).fill);
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
  await page.waitForFunction(id => document.querySelector('.lightbox-vector svg').id !== id, oldId);
  assert.notEqual(await page.locator('.lightbox-vector .node rect').first().evaluate(el => getComputedStyle(el).fill), oldFill);
});

test('CDP touch pinch, single-finger pan, double tap and cancellation on a phone', async t => {
  const page = await setup(t, true);
  await page.locator('.code-diagram').tap();
  const cdp = await page.context().newCDPSession(page);
  const touch = (type, points) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: points.map(([id, x, y]) => ({ id, x, y })) });
  await touch('touchStart', [[0, 150, 400], [1, 230, 400]]);
  for (let n = 1; n <= 6; n++) await touch('touchMove', [[0, 150 - n * 10, 400], [1, 230 + n * 10, 400]]);
  await touch('touchEnd', []);
  assert.ok((await view(page)).scale > 2, 'two fingers magnify');
  const before = await view(page);
  await touch('touchStart', [[0, 150, 400]]);
  await touch('touchMove', [[0, 250, 410]]);
  await touch('touchEnd', []);
  assert.ok((await view(page)).x > before.x, 'one finger pans after pinch');
  await touch('touchStart', [[0, 150, 400]]);
  await touch('touchCancel', []);
  assert.equal(await page.locator('.lightbox').count(), 1, 'cancel does not close');
  await page.getByRole('button', { name: '适应屏幕', exact: true }).tap();
  for (let n = 0; n < 2; n++) { await touch('touchStart', [[0, 195, 400]]); await touch('touchEnd', []); }
  assert.ok((await view(page)).scale > 1, 'double tap zooms');
  for (let n = 0; n < 2; n++) { await touch('touchStart', [[0, 195, 400]]); await touch('touchEnd', []); }
  assert.equal((await view(page)).scale, 1, 'double tap restores fit');
});

test('desktop double click and existing SVG image zoom and tap-to-close', async t => {
  const page = await setup(t, false, 'light', true);
  await open(page);
  await page.mouse.dblclick(720, 400);
  assert.ok((await view(page)).scale > 1);
  await page.mouse.dblclick(720, 400);
  assert.equal((await view(page)).scale, 1);
  await page.keyboard.down('Control');
  await page.mouse.wheel(0, -30);
  await page.keyboard.up('Control');
  await page.waitForFunction(() => new DOMMatrix(getComputedStyle(document.querySelector('.lightbox-vector')).transform).a > 1);
  assert.equal(await page.evaluate(() => window.visualViewport.scale), 1, 'trackpad-style wheel does not zoom the page');
  await page.keyboard.press('Escape');
  await page.locator('.code-picture').click();
  await page.locator('.lightbox img').waitFor();
  await page.getByRole('button', { name: '放大', exact: true }).click();
  assert.equal(await page.locator('.lightbox-scale').textContent(), '150%');
  await page.keyboard.press('0');
  await page.locator('.lightbox img').click();
  assert.equal(await page.locator('.lightbox').count(), 0);
});


test('an isolated SVG preserves accessible title and description references', async t => {
  const page = await setup(t);
  const result = await page.evaluate(async () => {
    const { enlargeMermaidDiagram } = await import('/src/mermaid.ts');
    const markup = enlargeMermaidDiagram('<svg xmlns="http://www.w3.org/2000/svg" id="diagram" aria-labelledby="title" aria-describedby="description"><title id="title">Architecture</title><desc id="description">Layers</desc><style>#diagram { color: red; }</style></svg>');
    const doc = new DOMParser().parseFromString(markup, 'image/svg+xml');
    return ['aria-labelledby', 'aria-describedby'].map(attribute => doc.getElementById(doc.documentElement.getAttribute(attribute))?.textContent);
  });
  assert.deepEqual(result, ['Architecture', 'Layers']);
});

test('formatted Mermaid labels survive the lazy SVG clone', async t => {
  const page = await setup(t);
  const result = await page.evaluate(async () => {
    const { renderMermaidDiagram, enlargeMermaidDiagram } = await import('/src/mermaid.ts');
    const svg = await renderMermaidDiagram('flowchart LR\nA["one & two<br/>next line"] --> B["space &nbsp; here"]');
    const markup = enlargeMermaidDiagram(svg);
    const container = document.createElement('div');
    container.innerHTML = markup;
    return { svg: !!container.querySelector('svg'), labels: container.querySelectorAll('.nodeLabel').length, text: container.querySelector('.nodeLabel')?.textContent, error: !!container.querySelector('parsererror') }; 
  });
  assert.deepEqual(result, { svg: true, labels: 2, text: 'one & twonext line', error: false });
});
