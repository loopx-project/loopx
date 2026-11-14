import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { APPEARANCE_KEY, DEFAULT_APPEARANCE, PALETTES, parseAppearance, resolveTheme } from '../src/appearance.js';

test('nothing saved, or something unreadable, gives the defaults', () => {
  assert.deepEqual(parseAppearance(null), DEFAULT_APPEARANCE);
  assert.deepEqual(parseAppearance('not json'), DEFAULT_APPEARANCE);
  assert.deepEqual(parseAppearance('"dark"'), DEFAULT_APPEARANCE);
  assert.deepEqual(parseAppearance('null'), DEFAULT_APPEARANCE);
});

test('a saved choice is read back', () => {
  assert.deepEqual(parseAppearance('{"mode":"dark","palette":"nord"}'), { mode: 'dark', palette: 'nord' });
});

test('each half falls back on its own', () => {
  // A palette from a newer build must not cost the user their light-or-dark choice, and the other way round.
  assert.deepEqual(parseAppearance('{"mode":"light","palette":"gone"}'), { mode: 'light', palette: 'paper' });
  assert.deepEqual(parseAppearance('{"mode":"dim","palette":"gruvbox"}'), { mode: 'system', palette: 'gruvbox' });
  assert.deepEqual(parseAppearance('{"mode":1,"palette":["nord"]}'), DEFAULT_APPEARANCE);
});

test('"follow the system" resolves against the system, a fixed mode does not', () => {
  assert.equal(resolveTheme('system', true), 'dark');
  assert.equal(resolveTheme('system', false), 'light');
  assert.equal(resolveTheme('light', true), 'light');
  assert.equal(resolveTheme('dark', false), 'dark');
});

test('every palette on offer has its colours in the stylesheet, light and dark', () => {
  const css = readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8');
  for (const { id } of PALETTES) {
    assert.ok(css.includes(`[data-palette='${id}']`), `${id} has no light colours`);
    assert.ok(css.includes(`[data-palette='${id}'][data-theme='dark']`), `${id} has no dark colours`);
  }
  // And the stylesheet has none the panel does not offer.
  const styled = new Set([...css.matchAll(/\[data-palette='([a-z-]+)'\]/g)].map((m) => m[1]));
  assert.deepEqual([...styled].sort(), PALETTES.map((p) => p.id).sort());
});

test('index.html reads the same storage key before the first paint', () => {
  const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  assert.ok(html.includes(`localStorage.getItem('${APPEARANCE_KEY}')`));
});
