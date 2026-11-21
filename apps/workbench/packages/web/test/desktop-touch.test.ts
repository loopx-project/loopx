import { test } from 'node:test';
import assert from 'node:assert/strict';
import { clampZoom, fillZoom, fitSize, isTap, pointerDelta, scrollAxis, scrollForZoom, softwareKeyboardUp, textEdit, twoFingerKind } from '../src/desktopTouch';

test('a wide desktop fits an upright phone by its width, and fills it by its height at a zoom', () => {
  const desktop = { w: 1920, h: 1200 };
  const phone = { w: 390, h: 700 };
  assert.deepEqual(fitSize(desktop, phone), { w: 390, h: 243.75 });
  assert.equal(fillZoom(desktop, phone), clampZoom(700 / 243.75));
  assert.deepEqual(fitSize({ w: 0, h: 0 }, phone), phone, 'before the desktop has a size');
});

test('zooming keeps the point under the fingers where it was', () => {
  // At zoom 1 the point 100px into the box, scrolled 0, is content 100; at zoom 2 it is 200.
  assert.equal(scrollForZoom(0, 100, 1, 2), 100);
  // Zooming back out never scrolls before the start.
  assert.equal(scrollForZoom(10, 100, 2, 1), 0);
  // A centred picture: its offset in the box is taken out, then the new one put back.
  assert.equal(scrollForZoom(0, 150, 1, 2, 50, 0), 50);
});

test('the touchpad is precise when slow and quick when flicked', () => {
  const slow = pointerDelta(10, 100, 5);
  const fast = pointerDelta(10, 5, 5);
  assert.ok(slow < fast);
  assert.ok(slow >= 10 * 5 * 0.5);
  assert.ok(fast <= 10 * 5 * 1.4);
  assert.equal(pointerDelta(-10, 100, 5), -slow, 'both ways alike');
});

test('taps, and what two fingers mean', () => {
  assert.equal(isTap(120, 3), true);
  assert.equal(isTap(400, 3), false, 'held: not a tap');
  assert.equal(isTap(120, 30), false, 'moved: not a tap');
  assert.equal(twoFingerKind(200, 205, 2), null, 'not clear yet');
  assert.equal(twoFingerKind(200, 260, 2), 'zoom');
  assert.equal(twoFingerKind(200, 205, 40), 'scroll');
  assert.equal(clampZoom(10), 6);
  assert.equal(clampZoom(0.5), 1);
});

test('the phone keyboard field: typed text, rubbed-out characters, and text put in the middle', () => {
  const fill = '____';
  assert.deepEqual(textEdit(fill, `${fill}a`), { erased: 0, typed: 'a' });
  assert.deepEqual(textEdit(fill, `${fill}你好`), { erased: 0, typed: '你好' });
  assert.deepEqual(textEdit(fill, '___'), { erased: 1, typed: '' });
  assert.deepEqual(textEdit(fill, `${fill}_`), { erased: 0, typed: '_' });
  // The caret moved two back: the new text is only the letter, not the fill after it.
  assert.deepEqual(textEdit(fill, '__x__'), { erased: 0, typed: 'x' });
  // An autocorrection replaces a word: rubbed out, then typed again.
  assert.deepEqual(textEdit(`${fill}teh`, `${fill}the`), { erased: 2, typed: 'he' });
  assert.deepEqual(textEdit(fill, fill), { erased: 0, typed: '' });
});

test('two fingers scroll along the way they first moved; a slant stays vertical', () => {
  assert.equal(scrollAxis(2, 30), 'y');
  assert.equal(scrollAxis(20, 16), 'y');
  assert.equal(scrollAxis(30, 4), 'x');
  assert.equal(scrollAxis(-30, -10), 'x');
});

test('the software keyboard is what shrinks the window, not a toolbar', () => {
  assert.equal(softwareKeyboardUp(844, 844), false, 'nothing covers the window');
  assert.equal(softwareKeyboardUp(844, 400), true, 'a keyboard covers nearly half of it');
  assert.equal(softwareKeyboardUp(844, 780), false, 'a toolbar shrinks it only a little');
  assert.equal(softwareKeyboardUp(844, null), false, 'no visual viewport reported');
});
