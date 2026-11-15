import { test } from 'node:test';
import assert from 'node:assert/strict';
import { externalPreview, isPreviewPick } from '../src/preview-notes';

test('external previews cannot use the authenticated app origin or URL credentials', () => {
  assert.equal(externalPreview('design.html', 'https://hub.example'), null);
  assert.equal(externalPreview('https://preview.example/', 'https://hub.example'), 'https://preview.example/');
  assert.throws(() => externalPreview('https://hub.example/settings', 'https://hub.example'));
  assert.throws(() => externalPreview('https://user:secret@preview.example/', 'https://hub.example'));
});

test('malformed and oversized postMessage picks are rejected before opening the editor', () => {
  assert.equal(isPreviewPick(null), false);
  assert.equal(isPreviewPick({ type: 'loochat-preview:picked', target: {} }), false);
  const pick = { target: { selector: '#status', text: 'devbox', html: '<span/>', rect: { x: 0, y: 0, width: 10, height: 10 } }, context: { title: '', url: '', theme: '', variant: '', revision: '', viewport: { width: 390, height: 844 } } };
  assert.equal(isPreviewPick(pick), true);
  assert.equal(isPreviewPick({ ...pick, target: { ...pick.target, html: 'x'.repeat(6001) } }), false);
  assert.equal(isPreviewPick({ ...pick, target: { ...pick.target, rect: { ...pick.target.rect, x: Infinity } } }), false);
});
