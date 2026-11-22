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
let searchTimeoutMs = 45_000
for (let index = 0; index < args.length; index += 2) {
  const [flag, value] = args.slice(index, index + 2)
  assert(
    value && (flag === '--tarball' || flag === '--registry' || flag === '--search-timeout-ms'),
    'usage: verify-distribution.mjs --tarball PATH [--registry URL] [--search-timeout-ms MS]',
  )
  if (flag === '--tarball') tarball = resolve(value)
  else if (flag === '--registry') registry = `${value.replace(/\/+$/u, '')}/`
  else {
    const parsed = Number(value)
    assert(Number.isFinite(parsed) && parsed >= 0, '--search-timeout-ms must be a non-negative number')
    searchTimeoutMs = parsed
  }
}
assert(tarball, '--tarball is required: compare the exact package approved for publication')

async function json(path) {
  const response = await fetch(new URL(path, registry), { signal: AbortSignal.timeout(15_000) })
  assert(response.ok, `registry readback failed: HTTP ${response.status}`)
  return response.json()
}

function repoSlug(url) {
  return String(url ?? '').replace(/^git\+/u, '').replace(/\.git$/u, '').toLowerCase()
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
const maintainers = [...new Set(
  [...(published.maintainers ?? []), ...(version.maintainers ?? [])]
    .map(entry => (typeof entry === 'string' ? entry : entry?.name ?? entry?.username))
    .filter(name => typeof name === 'string' && name.length > 0),
)].slice(0, 5)
const searchQueries = [`repository:${repo}`, ...maintainers.map(name => `maintainer:${name}`)]

async function searchRepositoryCandidates() {
  const merged = []
  const seen = new Set()
  for (const query of searchQueries) {
    const search = await json(`-/v1/search?text=${encodeURIComponent(query)}&size=250`)
    for (const item of search.objects ?? []) {
      const pkgName = item?.package?.name
      if (typeof pkgName !== 'string' || seen.has(pkgName)) continue
      seen.add(pkgName)
      merged.push(item)
    }
  }
  return merged.filter(({ package: pkg }) =>
    repoSlug(pkg.repository?.url ?? pkg.links?.repository).includes(`github.com/${repo.toLowerCase()}`))
}

// Match the market's repository-and-keyword discovery contract while retrying
// brief npm OpenSearch indexing delays and falling back to maintainer queries
// because npm's public search API does not restrict repository: qualifiers.
const deadline = Date.now() + searchTimeoutMs
let candidates = await searchRepositoryCandidates()
while (candidates.length === 0 && Date.now() < deadline) {
  await new Promise(resolveDelay => setTimeout(resolveDelay, Math.min(3_000, deadline - Date.now())))
  candidates = await searchRepositoryCandidates()
}
const selected = candidates.find(({ package: pkg }) =>
  pkg.keywords?.some(keyword => typeof keyword === 'string' && keyword.toLowerCase() === 'dsh-plugin'))
  ?? candidates[0]
assert.equal(selected?.package.name, manifest.name,
  'DSH Hub repository search does not yet select this plugin; publication is not marketplace-ready')
process.stdout.write(`verified ${manifest.name}@${manifest.version}: latest, artifact integrity, repository discovery\n`)
