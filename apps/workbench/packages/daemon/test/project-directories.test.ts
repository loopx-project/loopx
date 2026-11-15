import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { HttpError } from '../src/errors.js';
import { listProjectDirectories } from '../src/project-directories.js';

test('project browsing lists folders lazily without extending workdirs, and confines symlinks', () => {
  const root = mkdtempSync(join(tmpdir(), 'calli-project-browser-'));
  const home = join(root, 'home');
  const configured = join(root, 'configured');
  const outside = join(root, 'outside');
  for (const path of [home, configured, outside, join(home, 'project'), join(home, '.private'), join(home, 'node_modules')]) mkdirSync(path);
  writeFileSync(join(home, 'secret.txt'), 'not returned');
  symlinkSync(join(home, 'project'), join(home, 'alias'));
  symlinkSync(home, join(home, 'home-alias'));
  symlinkSync(outside, join(home, 'escape'));
  symlinkSync(join(root, 'missing'), join(home, 'broken'));
  const roots = [configured];
  try {
    const listed = listProjectDirectories(roots, undefined, home);
    assert.equal(listed.path, home);
    assert.equal(listed.parent, null);
    assert.equal(listed.allowed, false);
    assert.equal(listed.selectable, false);
    assert.deepEqual(listed.directories.map((d) => d.name), ['alias', 'home-alias', 'project']);
    assert.deepEqual(roots, [configured]);
    const project = listProjectDirectories(roots, join(home, 'project'), home);
    assert.equal(project.selectable, true);
    assert.equal(project.allowed, false);
    assert.equal(project.parent, home);
    assert.deepEqual(project.directories, []);
    assert.equal(listProjectDirectories(roots, join(home, 'home-alias'), home).selectable, false);
    assert.equal(listProjectDirectories(roots, configured, home).parent, null);
    assert.equal(listProjectDirectories(roots, configured, home).allowed, true);
    for (const path of [outside, join(home, '..'), join(home, 'escape')]) {
      assert.throws(() => listProjectDirectories(roots, path, home), (e) => e instanceof HttpError && e.status === 403);
    }
    assert.throws(() => listProjectDirectories(roots, 'relative', home), (e) => e instanceof HttpError && e.status === 400);
    for (const path of [join(home, 'gone'), join(home, 'secret.txt'), join(home, 'broken')]) {
      assert.throws(() => listProjectDirectories(roots, path, home), (e) => e instanceof HttpError && e.status === 404);
    }
    let deep = join(home, 'project');
    for (let i = 0; i < 8; i++) {
      deep = join(deep, 'nested');
      mkdirSync(deep);
    }
    assert.equal(listProjectDirectories(roots, deep, home).selectable, true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('project browsing takes ~ for the home directory, and says where home is', () => {
  const root = mkdtempSync(join(tmpdir(), 'calli-project-tilde-'));
  const home = join(root, 'home');
  const project = join(home, 'project');
  mkdirSync(project, { recursive: true });
  try {
    const listed = listProjectDirectories([], '~', home);
    assert.equal(listed.path, home);
    assert.equal(listed.home, home, 'the trail starts at home');
    const inside = listProjectDirectories([], '~/project', home);
    assert.equal(inside.path, project);
    assert.equal(inside.parent, home);
    assert.throws(() => listProjectDirectories([], '~nope/relative', home), (e) => e instanceof HttpError && e.status === 400);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
