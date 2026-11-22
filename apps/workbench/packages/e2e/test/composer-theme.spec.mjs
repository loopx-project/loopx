import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { tsImport } from 'tsx/esm/api';
import { launchBrowser } from '../harness.mjs';

const { PALETTES } = await tsImport('../../web/src/appearance.ts', import.meta.url);
const css = readFileSync(new URL('../../web/src/styles.css', import.meta.url), 'utf8');

// The scope is intentional: only Codex light changes. Unrelated themes retain
// their existing surface, filled queue rows and desktop/mobile material.
test('composer correction stays in Codex light across every shipped palette', async t => {
  const browser = await launchBrowser();
  const page = await browser.newPage();
  try {
    for (const { id } of PALETTES) for (const theme of ['light', 'dark']) for (const width of [1280, 390]) {
      await t.test(`${id} ${theme} ${width}`, async () => {
        await page.setViewportSize({ width, height: 844 });
        await page.emulateMedia({ colorScheme: theme });
        await page.setContent(`<html data-palette="${id}" data-theme="${theme}"><head><style>${css}</style></head><body>
          <main class="main"><div class="composer"><div class="composer-box">
            <div class="composer-context">Workspace</div>
            <div class="queue"><div class="queue-item"><span class="queue-badge">Queued</span>Keep the queued message readable.</div></div>
            <textarea aria-label="Draft"></textarea>
          </div></div></main></body></html>`);
        const actual = await page.evaluate(() => {
          const style = selector => getComputedStyle(document.querySelector(selector));
          const probe = document.createElement('div'); document.body.append(probe);
          const token = (property, name) => {
            probe.style.setProperty(property, `var(${name})`);
            return getComputedStyle(probe).getPropertyValue(property);
          };
          const values = {
            fill: style('.composer-box').backgroundColor, queue: style('.queue-item').backgroundColor,
            context: style('.composer-context').backgroundColor, shadow: style('.composer-box').boxShadow,
            blur: style('.composer-box').backdropFilter, queueBottom: style('.queue').paddingBottom,
            canvas: token('background-color', '--bg'), surface: token('background-color', '--surface'),
            row: token('background-color', '--surface-2'), glass: token('background-color', '--glass-bg'),
            halo: token('box-shadow', '--glass-halo'), glassShadow: token('box-shadow', '--glass-shadow'),
            glassFilter: token('backdrop-filter', '--glass-filter'),
          };
          probe.remove(); return values;
        });
        if (id === 'codex' && theme === 'light') {
          assert.equal(actual.fill, actual.canvas);
          assert.equal(actual.queue, 'rgba(0, 0, 0, 0)');
          assert.equal(actual.context, 'rgba(0, 0, 0, 0)');
          assert.equal(actual.shadow, 'none');
          assert.equal(actual.blur, 'none');
          assert.equal(actual.queueBottom, '8px');
        } else {
          assert.equal(actual.fill, width >= 900 ? actual.surface : actual.glass);
          assert.equal(actual.queue, actual.row);
          assert.equal(actual.context, actual.row);
          assert.equal(actual.shadow, width >= 900 ? actual.halo : actual.glassShadow);
          assert.equal(actual.blur, width >= 900 ? 'none' : actual.glassFilter);
          assert.equal(actual.queueBottom, '0px');
        }
      });
    }
  } finally { await browser.close(); }
});
