#!/usr/bin/env node

import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { chmod, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const manifest = JSON.parse(await readFile(join(packageRoot, 'package.json'), 'utf8'))
const dshBin = process.env.DSH_BIN || join(packageRoot, 'node_modules', '.bin', 'dsh')
const args = process.argv.slice(2)
assert(args.length === 0 || (args.length === 2 && args[0] === '--previous-tarball'),
  'usage: dsh-installer-smoke.mjs [--previous-tarball PATH]')
const previousTarball = args.length === 2 ? resolve(args[1]) : undefined
const temp = await mkdtemp(join(tmpdir(), 'dsh-loopx-installer-'))
const home = join(temp, 'dsh-home')
const env = { ...process.env, DSH_HOME: home, DSH_AGENTS_HOME: join(temp, 'agents'), DSH_BIN: dshBin }
const installedManifest = join(home, 'profiles', 'web', 'node_modules', manifest.name, 'package.json')

function run(file, argv, environment = env) {
  return spawnSync(file, argv, {
    cwd: temp, env: environment, encoding: 'utf8', shell: false,
    timeout: 180_000, maxBuffer: 8 * 1024 * 1024,
  })
}

function expectSuccess(result) {
  assert.ifError(result.error)
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`)
}

try {
  if (previousTarball) {
    expectSuccess(run(dshBin, ['plugin', '--profile', 'web', 'add', previousTarball,
      '--prefer-offline', '--ignore-scripts']))
    const previous = JSON.parse(await readFile(installedManifest, 'utf8'))
    assert.equal(previous.name, manifest.name)
    assert.notEqual(previous.version, manifest.version, 'upgrade requires a different previous version')
  }

  // Invoke the shipped entry from outside the checkout, rather than mirroring
  // its profile checks. This catches a stale row set or false upgrade success.
  const installed = run('bash', [join(packageRoot, 'install.sh')])
  expectSuccess(installed)
  assert(installed.stdout.includes(`installed and verified ${manifest.name}@${manifest.version}`))
  const installedText = await readFile(installedManifest, 'utf8')
  const readback = JSON.parse(installedText)
  assert.equal(readback.name, manifest.name)
  assert.equal(readback.version, manifest.version)

  // An incompatible version must fail before any DSH profile operation. The
  // shim represents only host version reporting, never a successful runtime.
  const unsupported = join(temp, 'unsupported-dsh')
  const marker = join(temp, 'unexpected-profile-operation')
  await writeFile(unsupported, `#!/bin/sh\nif [ "$1" = '--version' ]; then\n  printf '%s\\n' '0.2.0-rc.2'\nelse\n  touch "$LOOPX_INSTALLER_SMOKE_MARKER"\n  exit 1\nfi\n`)
  await chmod(unsupported, 0o755)
  const rejected = run('bash', [join(packageRoot, 'install.sh')], {
    ...env, DSH_BIN: unsupported, LOOPX_INSTALLER_SMOKE_MARKER: marker,
  })
  assert.ifError(rejected.error)
  assert.equal(rejected.status, 2)
  assert(rejected.stderr.includes('outside the supported peer range'))
  assert.equal(await stat(marker).then(() => true, () => false), false)
  assert.equal(await readFile(installedManifest, 'utf8'), installedText)

  expectSuccess(run(dshBin, ['plugin', '--profile', 'web', 'remove', manifest.name]))
  const removed = run(dshBin, ['--profile', 'web', '--dump-config'])
  expectSuccess(removed)
  assert(!removed.stdout.includes('name: dsh-loopx-plugin'))
  process.stdout.write(`dsh-loopx installer smoke passed (${previousTarball ? 'upgrade' : 'fresh install'}, incompatible-host rejection, removal)\n`)
} finally {
  await rm(temp, { recursive: true, force: true })
}
