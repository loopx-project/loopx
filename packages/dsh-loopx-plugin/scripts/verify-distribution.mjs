#!/usr/bin/env node

// Read back the registry artifact and the repository search used by DSH Hub.
// This verifies a publication; it never publishes or changes a dist-tag.
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const manifest = JSON.parse(await readFile(join(packageRoot, 'package.json'), 'utf8'))
const args = process.argv.slice(2)
let tarball
let registry = 'https://registry.npmjs.org/'
for (let index = 0; index < args.length; index += 2) {
  const [flag, value] = args.slice(index, index + 2)
  assert(value && (flag === '--tarball' || flag === '--registry'),
    'usage: verify-distribution.mjs --tarball PATH [--registry URL]')
  if (flag === '--tarball') tarball = resolve(value)
  else registry = `${value.replace(/\/+$/u, '')}/`
}
assert(tarball, '--tarball is required: compare the exact package approved for publication')

async function json(path) {
  const response = await fetch(new URL(path, registry), { signal: AbortSignal.timeout(15_000) })
  assert(response.ok, `registry readback failed: HTTP ${response.status}`)
  return response.json()
}

const published = await json(encodeURIComponent(manifest.name))
const version = published.versions?.[manifest.version]
assert(version, `registry has no ${manifest.name}@${manifest.version}`)
assert.equal(published['dist-tags']?.latest, manifest.version,
  'DSH Hub installs the unversioned package: latest must name the qualified release')
for (const field of ['name', 'version', 'main', 'exports', 'dsh', 'peerDependencies', 'repository', 'keywords']) {
  assert.deepEqual(version[field], manifest[field], `published ${field} differs from the source candidate`)
}
assert(version.keywords.includes('dsh-plugin'), 'DSH Hub needs the dsh-plugin discovery keyword')
const artifact = await readFile(tarball)
const integrity = `sha512-${createHash('sha512').update(artifact).digest('base64')}`
assert.equal(version.dist?.integrity, integrity, 'registry integrity differs from the approved tarball')
const remote = await fetch(version.dist.tarball, { signal: AbortSignal.timeout(30_000) })
assert(remote.ok, `registry tarball download failed: HTTP ${remote.status}`)
assert.equal(`sha512-${createHash('sha512').update(Buffer.from(await remote.arrayBuffer())).digest('base64')}`,
  integrity, 'downloaded registry artifact differs from the approved tarball')

const repo = new URL(manifest.repository.url.replace(/^git\+/u, '').replace(/\.git$/u, '')).pathname.slice(1)
const search = await json(`-/v1/search?text=${encodeURIComponent(`repository:${repo}`)}&size=250`)
// Match the market's repository-and-keyword discovery contract, independently
// of the package name appearing in an unrelated search result.
const candidates = (search.objects ?? []).filter(({ package: pkg }) =>
  typeof pkg?.name === 'string'
  && String(pkg.repository?.url ?? pkg.links?.repository ?? '').toLowerCase()
    .includes(`github.com/${repo}`.toLowerCase()))
const selected = candidates.find(({ package: pkg }) =>
  pkg.keywords?.some(keyword => typeof keyword === 'string' && keyword.toLowerCase() === 'dsh-plugin'))
  ?? candidates[0]
assert.equal(selected?.package.name, manifest.name,
  'DSH Hub repository search does not yet select this plugin; publication is not marketplace-ready')
process.stdout.write(`verified ${manifest.name}@${manifest.version}: latest, artifact integrity, repository discovery\n`)
