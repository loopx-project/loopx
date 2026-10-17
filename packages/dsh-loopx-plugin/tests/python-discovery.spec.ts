import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { delimiter, join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { pluginPythonCandidates } from '../src/managed-runtime.ts'

describe('installed Python discovery', () => {
  it('discovers unlisted minors in numeric order without executable suffix lookalikes', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'loopx-python-discovery-'))
    try {
      for (const name of ['python3.9', 'python3.97', 'python3.12', 'python3.999-config']) {
        await writeFile(join(directory, name), '# fixture executable\n')
      }
      await mkdir(join(directory, 'python3.998'))
      expect(pluginPythonCandidates({ env: { PATH: directory } })).toEqual([
        'python3', 'python3.97', 'python3.12', 'python3.9', 'python',
      ])
      expect(pluginPythonCandidates({ env: { PATH: `${directory}${delimiter}${directory}` } }))
        .toEqual(['python3', 'python3.97', 'python3.12', 'python3.9', 'python'])
    } finally {
      await rm(directory, { recursive: true, force: true })
    }
  })

  it('discovers a versioned Windows interpreter and ignores a bare-name alias stub', async () => {
    const alias = await mkdtemp(join(tmpdir(), 'loopx-python-alias-'))
    const host = await mkdtemp(join(tmpdir(), 'loopx-python-host-'))
    try {
      // The WindowsApps alias directory carries a stub python3.exe that starts
      // and exits 9009 without running Python and names no minor version; a
      // Windows host can also register python3.12.exe next to python.exe.
      await writeFile(join(alias, 'python3.exe'), '')
      await writeFile(join(host, 'python3.12.exe'), '# fixture executable\n')
      await writeFile(join(host, 'python.exe'), '# fixture executable\n')
      expect(pluginPythonCandidates({ env: { PATH: `${alias}${delimiter}${host}` } }))
        .toEqual(['python3', 'python3.12.exe', 'python'])
    } finally {
      await rm(alias, { recursive: true, force: true })
      await rm(host, { recursive: true, force: true })
    }
  })

  it('keeps an explicit interpreter authoritative and tolerates missing PATH directories', () => {
    expect(pluginPythonCandidates({ pythonBin: '/configured/python', env: { PATH: '/missing' } }))
      .toEqual(['/configured/python'])
    expect(pluginPythonCandidates({ env: { PATH: '/missing', PYTHON_BIN: '/custom/python' } }))
      .toEqual(['/custom/python'])
    expect(pluginPythonCandidates({ env: { PATH: '/missing' } })).toEqual(['python3', 'python'])
  })
})
