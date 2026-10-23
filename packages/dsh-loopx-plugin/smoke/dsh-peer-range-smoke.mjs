#!/usr/bin/env node

import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runInNewContext } from 'node:vm'
import { minVersion, satisfies, validRange } from 'semver'

const packageRoot = dirname(dirname(fileURLToPath(import.meta.url)))
const manifest = JSON.parse(await readFile(join(packageRoot, 'package.json'), 'utf8'))
const runtimeManifest = JSON.parse(await readFile(join(packageRoot, '../../package.json'), 'utf8'))
assert.equal(minVersion(manifest.engines.node).version, minVersion(runtimeManifest.engines.node).version,
  'the plugin must not advertise a Node version below the LoopX runtime minimum')

const installer = await readFile(join(packageRoot, 'install.sh'), 'utf8')
const guard = installer.match(/node -e '([\s\S]*?)'\s*\|\|/)
assert(guard, 'the source installer must check Node before installing dependencies')
for (const [version, expected] of [
  ['22.19.0', false], ['22.22.2', false], ['22.22.3-rc.1', false],
  ['22.22.3', true], ['22.23.0', true], ['22.23.0-rc.1', false],
  ['23.0.0', false], ['24.0.0-rc.1', false], ['24.0.0', true], ['24.20.0', true],
]) {
  assert.equal(satisfies(version, manifest.engines.node), expected, `package Node range: ${version}`)
  const rejected = Symbol('unsupported Node')
  let admitted = true
  try {
    runInNewContext(guard[1], { process: {
      versions: { node: version },
      exit(code) { assert.equal(code, 1); throw rejected },
    } })
  } catch (error) {
    if (error !== rejected) throw error
    admitted = false
  }
  assert.equal(admitted, expected, `source installer Node guard: ${version}`)
}

for (const [name, range] of Object.entries(manifest.peerDependencies ?? {})) {
  if (!name.startsWith('@deepseek-ai/')) continue
  assert(validRange(range), `invalid peer range for ${name}: ${range}`)
  const testedVersion = manifest.devDependencies?.[name]
  assert(testedVersion, `missing tested version for official peer ${name}`)
  assert(satisfies(testedVersion, range), `${name} excludes pinned host ${testedVersion}`)
  if (!name.startsWith('@deepseek-ai/dsh')) continue
  // SemVer excludes prereleases of a different tuple unless that tuple is
  // explicitly declared. Each prerelease tuple has packed-host qualification;
  // do not opt every future release candidate into compatibility.
  for (const version of [
    '0.1.5-rc.1', '0.1.5-rc.2', '0.1.7-rc.2', '0.1.7',
    '0.2.0-rc.2', '0.2.0', '0.2.1-rc.1', '0.2.1-rc.2',
  ]) {
    assert(satisfies(version, range), `${name} excludes supported host ${version}`)
  }
  for (const version of ['0.1.4', '0.1.5-rc.0', '0.1.7-rc.1', '0.1.8-rc.1', '0.2.0-rc.1', '0.2.1-alpha.1', '0.2.1', '0.3.0']) {
    assert(!satisfies(version, range), `${name} admits unqualified host ${version}`)
  }
}

process.stdout.write('dsh-loopx peer range smoke passed\n')
