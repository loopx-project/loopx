import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { build } from 'vite';
import config from '../vite.config.js';

test('a failed web build preserves the published homepage and assets', async () => {
  const root = await mkdtemp(join(tmpdir(), 'calli-build-'));
  try {
    await mkdir(join(root, 'dist', 'assets'), { recursive: true });
    await writeFile(join(root, 'index.html'), '<html>new page</html>');
    await writeFile(join(root, 'dist', 'index.html'), 'published page');
    await writeFile(join(root, 'dist', 'assets', 'old.js'), 'published asset');
    await assert.rejects(build({
      ...config,
      root,
      configFile: false,
      logLevel: 'silent',
      plugins: [{
        name: 'interrupt-build',
        generateBundle() { throw new Error('simulated build interruption'); },
      }],
    }), /simulated build interruption/);
    assert.equal(await readFile(join(root, 'dist', 'index.html'), 'utf8'), 'published page');
    assert.equal(await readFile(join(root, 'dist', 'assets', 'old.js'), 'utf8'), 'published asset');
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
