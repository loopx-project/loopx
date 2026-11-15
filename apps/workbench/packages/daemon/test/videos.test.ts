import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { MAX_VIDEO_BYTES, sniffVideoType, storeVideo } from '../src/videos.js';

const mp4 = readFileSync(new URL('./fixtures/sample-video.mp4', import.meta.url));

test('video signatures accept MP4, MOV and WebM but not images disguised as videos', () => {
  assert.equal(sniffVideoType(mp4), 'video/mp4');
  const mov = Buffer.from(mp4); mov.write('qt  ', 8, 'ascii');
  assert.equal(sniffVideoType(mov), 'video/quicktime');
  assert.equal(sniffVideoType(Buffer.from([0x1a, 0x45, 0xdf, 0xa3, ...Buffer.from('doctype:webm')])), 'video/webm');
  const image = Buffer.from(mp4); image.write('avif', 8, 'ascii');
  for (const data of [image, Buffer.from('not a video'), Buffer.from('<svg></svg>'), Buffer.alloc(0)]) assert.equal(sniffVideoType(data), null);
});

test('video streaming preserves bytes, cleans interrupted uploads, and enforces size limits', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'calli-video-'));
  const base = join(dir, 'upload');
  try {
    const info = await storeVideo(base, (async function* () { yield mp4.subarray(0, 3); yield mp4.subarray(3); })(), '../录屏.mp4');
    assert.equal(info.name, '录屏.mp4');
    assert.deepEqual(readFileSync(`${base}.mp4`), mp4);
    rmSync(`${base}.mp4`); rmSync(`${base}.json`);
    await assert.rejects(storeVideo(base, (async function* () { yield mp4; throw new Error('disconnected'); })(), 'video.mp4'), /disconnected/);
    assert.deepEqual(readdirSync(dir), []);
    await assert.rejects(storeVideo(base, (async function* () { yield Buffer.alloc(MAX_VIDEO_BYTES + 1); })(), 'too-big.mp4'), /100 MB/);
    assert.deepEqual(readdirSync(dir), []);
    await assert.rejects(storeVideo(base, (async function* () { yield Buffer.from('fake'); })(), 'fake.mp4'), /MP4/);
    assert.deepEqual(readdirSync(dir), []);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
