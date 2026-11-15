import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MAX_ZOOM, REST, fitted, panBy, zoomAt } from '../src/zoom.js';

const size = { width: 800, height: 600 };
const centre = { x: 0, y: 0 };

test('zooming keeps the spot under the pointer where it is', () => {
  const point = { x: 200, y: -100 };
  const once = zoomAt(REST, 2, point, size);
  assert.deepEqual(once, { scale: 2, x: -200, y: 100 });
  // The spot of the picture under the pointer: (point - offset) / scale, the same before and after.
  const under = (v: typeof once) => [(point.x - v.x) / v.scale, (point.y - v.y) / v.scale];
  assert.deepEqual(under(once), under(REST));
  assert.deepEqual(under(zoomAt(once, 1.5, point, size)), under(REST));
  assert.deepEqual(zoomAt(REST, 2, centre, size), { scale: 2, x: 0, y: 0 });
});

test('zoom stays between the fitted size and the limit; back at rest the picture is centred again', () => {
  assert.equal(zoomAt(REST, 100, centre, size).scale, MAX_ZOOM);
  assert.deepEqual(zoomAt(REST, 0.5, { x: 300, y: 300 }, size), REST);
  const moved = panBy(zoomAt(REST, 2, centre, size), 150, -80, size);
  assert.deepEqual(moved, { scale: 2, x: 150, y: -80 });
  assert.deepEqual(zoomAt(moved, 0.25, { x: 10, y: 10 }, size), REST);
});

test('a picture cannot be dragged off: an edge stops where the opposite one rests', () => {
  const twice = zoomAt(REST, 2, centre, size);
  assert.deepEqual(panBy(twice, 5000, -5000, size), { scale: 2, x: 400, y: -300 });
  assert.deepEqual(panBy(REST, 50, 50, size), REST, 'nothing to drag at rest');
  // Zooming out near an edge pulls the picture back within bounds.
  const corner = panBy(zoomAt(REST, 4, centre, size), 5000, 5000, size);
  assert.deepEqual(corner, { scale: 4, x: 1200, y: 900 });
  const out = zoomAt(corner, 0.5, centre, size);
  assert.deepEqual(out, { scale: 2, x: 400, y: 300 });
});

test('a picture rests as large as the room allows: a bitmap up to its own size, a drawing all the way', () => {
  const room = { width: 1400, height: 800 };
  assert.deepEqual(fitted({ width: 2800, height: 1000 }, room, false), { width: 1400, height: 500 });
  assert.deepEqual(fitted({ width: 1000, height: 2000 }, room, false), { width: 400, height: 800 });
  assert.deepEqual(fitted({ width: 200, height: 100 }, room, false), { width: 200, height: 100 }, 'a small bitmap is not blown up');
  assert.deepEqual(fitted({ width: 24, height: 24 }, room, true), { width: 800, height: 800 }, 'an icon drawn in vectors is');
  assert.deepEqual(fitted({ width: 1200, height: 800 }, room, true), { width: 1200, height: 800 });
  assert.deepEqual(fitted({ width: 0, height: 0 }, room, true), { width: 0, height: 0 });
});

// A diagram wider than eight phone screens must still reach its native text size.
test('a vector viewer can allow native scale beyond the default image zoom limit', () => {
  const phone = { width: 350, height: 60 };
  const native = 4200 / phone.width;
  const detail = zoomAt(REST, native, centre, phone, native * 2);
  assert.equal(detail.scale, 12);
  assert.equal(zoomAt(detail, 10, centre, phone, native * 2).scale, 24);
  assert.deepEqual(zoomAt(detail, 1 / native, centre, phone, native * 2), REST);
});
