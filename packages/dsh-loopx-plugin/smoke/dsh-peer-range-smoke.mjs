#!/usr/bin/env node

import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { satisfies, validRange } from 'semver'

const packageRoot = dirname(dirname(fileURLToPath(import.meta.url)))
const manifest = JSON.parse(await readFile(join(packageRoot, 'package.json'), 'utf8'))

for (const [name, range] of Object.entries(manifest.peerDependencies ?? {})) {
  if (!name.startsWith('@deepseek-ai/')) continue
  assert(validRange(range), `invalid peer range for ${name}: ${range}`)
  const testedVersion = manifest.devDependencies?.[name]
  assert(testedVersion, `missing tested version for official peer ${name}`)
  assert(satisfies(testedVersion, range), `${name} excludes pinned host ${testedVersion}`)
  if (!name.startsWith('@deepseek-ai/dsh')) continue
  // SemVer excludes prereleases of a different tuple unless that tuple is
  // explicitly declared. The 0.1.5 and 0.1.7 candidates have packed-host qualification;
  // do not opt every future release candidate into compatibility.
  for (const version of ['0.1.5-rc.1', '0.1.5-rc.2', '0.1.7-rc.2', '0.1.7']) {
    assert(satisfies(version, range), `${name} excludes supported host ${version}`)
  }
  for (const version of ['0.1.4', '0.1.5-rc.0', '0.1.7-rc.1', '0.1.8-rc.1', '0.2.0-rc.2', '0.2.0']) {
    assert(!satisfies(version, range), `${name} admits unqualified host ${version}`)
  }
}

process.stdout.write('dsh-loopx peer range smoke passed\n')
