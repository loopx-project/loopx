import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fitWithin, pictureFileName, pngFileName, svgPicture } from '../src/images.js';

test('pictures within the limit keep their size', () => {
  assert.deepEqual(fitWithin(800, 600, 2048), { width: 800, height: 600 });
  assert.deepEqual(fitWithin(2048, 2048, 2048), { width: 2048, height: 2048 });
});

test('larger pictures are scaled so the longest edge meets the limit, keeping their shape', () => {
  assert.deepEqual(fitWithin(4096, 2048, 2048), { width: 2048, height: 1024 });
  assert.deepEqual(fitWithin(3024, 4032, 2048), { width: 1536, height: 2048 });
});

test('a very thin picture never gets a zero edge', () => {
  assert.deepEqual(fitWithin(10000, 2, 2048), { width: 2048, height: 1 });
});

const drawn = (code: string) => {
  const url = svgPicture(code);
  return url === null ? null : decodeURIComponent(url.replace('data:image/svg+xml;charset=utf-8,', ''));
};

test('svgPicture: a code block that holds one whole SVG document is a picture', () => {
  const whole = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">\n  <circle r="5"/>\n</svg>';
  assert.equal(drawn(whole), whole);
  assert.equal(drawn(`\n${whole}\n\n`), whole, 'the blank lines around it do not count');
  assert.match(svgPicture(whole)!, /^data:image\/svg\+xml;charset=utf-8,%3Csvg/);
  const prefaced = `<?xml version="1.0" encoding="UTF-8"?>\n<!-- a dinosaur -->\n<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "x.dtd">\n${whole}`;
  assert.equal(drawn(prefaced), prefaced);
});

test('svgPicture: namespaces a web page would forgive are put back, since an <img> does not', () => {
  assert.equal(drawn('<svg viewBox="0 0 1 1"><rect/></svg>'), '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect/></svg>');
  assert.equal(drawn('<svg><rect/></svg>'), '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>');
  assert.equal(
    drawn('<!-- x --><svg xmlns="http://www.w3.org/2000/svg"><use xlink:href="#a"/></svg>'),
    '<!-- x --><svg xmlns:xlink="http://www.w3.org/1999/xlink" xmlns="http://www.w3.org/2000/svg"><use xlink:href="#a"/></svg>',
  );
  const both = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><use xlink:href="#a"/></svg>';
  assert.equal(drawn(both), both);
});

test('svgPicture: anything else stays code', () => {
  for (const code of [
    '',
    'const a = 1;',
    '<svg xmlns="http://www.w3.org/2000/svg"><circle r="5"/>', // still being written
    '<div><svg></svg></div>',
    '<html><body><svg></svg></body></html>',
    'Here it is: <svg></svg>',
    '<svgfoo></svg>',
    '<svg/>',
  ]) {
    assert.equal(svgPicture(code), null, code);
  }
  assert.equal(svgPicture(`<svg>${' '.repeat(2_000_001)}</svg>`), null, 'too large to be a drawing');
});

test('a saved picture gets a file name with the ending its type calls for', () => {
  assert.equal(pictureFileName('/work/app/out/dino.svg', 'image/svg+xml'), 'dino.svg');
  assert.equal(pictureFileName('chart.PNG', 'image/png'), 'chart.PNG');
  assert.equal(pictureFileName('图片', 'image/jpeg'), '图片.jpg');
  assert.equal(pictureFileName(undefined, 'image/webp'), 'image.webp');
  assert.equal(pictureFileName('  ', 'application/octet-stream'), 'image.png');
  assert.equal(pngFileName('dino.svg'), 'dino.png');
  assert.equal(pngFileName('v1.2 草图'), 'v1.2 草图.png', 'not every dot starts an ending');
});
