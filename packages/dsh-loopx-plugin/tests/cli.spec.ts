import { describe, expect, it } from 'vitest'
import {
  LoopXCliError,
  resolveLoopXCommand,
  runFile,
  runJsonCommand,
  runJsonMutationCommand,
} from '../src/cli.ts'
import type { FileRunner, LoopXCommand } from '../src/cli.ts'

const command: LoopXCommand = {
  file: 'loopx',
  prefix: [],
  skillCommand: 'loopx',
  version: 'loopx 0.5.0',
}

describe('runJsonCommand', () => {
  it.each(['0.5.4', '1.2.3', '1.2.4rc1', '1.2.4.dev1', 'smoke', '1.2.4unknown'])(
    'rejects unavailable or pre-fix CLI version %s', async version => {
      await expect(resolveLoopXCommand({
        env: { LOOPX_BIN: 'configured-loopx' },
        runner: async () => ({ exitCode: 0, stdout: `loopx ${version}\n`, stderr: '' }),
      })).rejects.toMatchObject({ kind: 'missing', retryable: false })
    },
  )

  it.each(['1.2.4', '1.2.4+local', '1.2.4.post1', '1.2.5rc1', '2.0.0.dev1'])(
    'accepts CLI release %s at or after the fixed floor', async version => {
      const command = await resolveLoopXCommand({
        env: { LOOPX_BIN: 'configured-loopx' },
        runner: async () => ({ exitCode: 0, stdout: `loopx ${version}\n`, stderr: '' }),
      })
      expect(command.version).toBe(`loopx ${version}`)
    },
  )

  it('skips an old global CLI for a compatible Python module', async () => {
    const command = await resolveLoopXCommand({
      env: { PYTHON_BIN: 'fixture-python', LOOPX_BIN: '' },
      runner: async file => ({
        exitCode: 0, stdout: `loopx ${file === 'loopx' ? '1.2.3' : '1.2.4'}\n`, stderr: '',
      }),
    })
    expect(command.file).toBe('fixture-python')
    expect(command.prefix).toEqual(['-m', 'loopx.cli'])
  })

  it('keeps a configured LoopX executable as one quoted shell word', async () => {
    const configured = '/opt/Loop X/loopx'
    const resolved = await resolveLoopXCommand({
      env: { ...process.env, LOOPX_BIN: configured },
      runner: async (file, args) => {
        expect(file).toBe(configured)
        expect(args).toEqual(['--version'])
        return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
      },
    })

    expect(resolved.skillCommand).toBe('"$LOOPX_BIN"')
  })

  it('renders the configured Python fallback as one shell-safe skill command', async () => {
    const python = "/opt/Loop X/python'3"
    const resolved = await resolveLoopXCommand({
      env: { ...process.env, PYTHON_BIN: python },
      runner: async (file, args) => {
        if (file === 'loopx') return { exitCode: 127, stdout: '', stderr: '' }
        expect(file).toBe(python)
        expect(args).toEqual(['-m', 'loopx.cli', '--version'])
        return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
      },
    })

    expect(resolved.skillCommand).toBe(`'/opt/Loop X/python'"'"'3' -m loopx.cli`)
  })

  it('prefers a managed launcher and quotes both runtime paths for skills', async () => {
    const python = "/opt/Python 3/python'3"
    const launcher = "/opt/DSH LoopX/runtime'1/loopx_cli.py"
    const resolved = await resolveLoopXCommand({
      managedLauncher: { path: launcher, pythonBins: [python] },
      runner: async (file, args) => {
        expect(file).toBe(python)
        expect(args).toEqual([launcher, '--version'])
        return { exitCode: 0, stdout: 'loopx 1.2.4\n', stderr: '' }
      },
    })

    expect(resolved.skillCommand).toBe(
      `'/opt/Python 3/python'"'"'3' '/opt/DSH LoopX/runtime'"'"'1/loopx_cli.py'`,
    )
  })

  it('retries transport-safe fixed argv without changing the request', async () => {
    const calls: string[][] = []
    const runner: FileRunner = async (_file, args) => {
      calls.push([...args])
      if (calls.length < 3) {
        throw new LoopXCliError('timeout', 'timed out', true)
      }
      return { exitCode: 0, stdout: '{"ok":true,"value":7}', stderr: '' }
    }

    const payload = await runJsonCommand(command, ['quota', 'should-run'], {
      runner,
      attempts: 3,
      retryDelaysMs: [0, 0],
    })

    expect(payload).toEqual({ ok: true, value: 7 })
    expect(calls).toEqual([
      ['quota', 'should-run'],
      ['quota', 'should-run'],
      ['quota', 'should-run'],
    ])
  })

  it('returns a typed nonzero authority response without retrying', async () => {
    let calls = 0
    const runner: FileRunner = async () => {
      calls += 1
      return {
        exitCode: 1,
        stdout: '{"ok":false,"error_kind":"authority_denied"}',
        stderr: 'private diagnostic must not be parsed',
      }
    }

    const payload = await runJsonCommand(command, ['quota', 'should-run'], {
      runner,
      attempts: 3,
    })

    expect(payload.error_kind).toBe('authority_denied')
    expect(calls).toBe(1)
  })

  it('does not retry an incompatible typed schema', async () => {
    let calls = 0
    const runner: FileRunner = async () => {
      calls += 1
      return { exitCode: 0, stdout: '{"schema_version":"old"}', stderr: '' }
    }

    await expect(runJsonCommand(command, ['inspect'], {
      runner,
      attempts: 3,
      validate: payload => payload.schema_version === 'current',
    })).rejects.toMatchObject({ kind: 'invalid_schema', retryable: false })
    expect(calls).toBe(1)
  })
})

describe('reap-aware mutation execution', () => {
  it('waits for child close after timeout termination', async () => {
    const startedAt = Date.now()
    await expect(runFile(process.execPath, [
      '-e',
      [
        "process.on('SIGTERM',()=>setTimeout(()=>process.exit(0),80))",
        'setInterval(()=>{},1000)',
      ].join(';'),
    ], {
      timeoutMs: 300,
      maxOutputBytes: 1024,
    })).rejects.toMatchObject({ kind: 'timeout' })

    expect(Date.now() - startedAt).toBeGreaterThanOrEqual(350)
  })

  it('waits for child close after output-limit termination', async () => {
    const startedAt = Date.now()
    await expect(runFile(process.execPath, [
      '-e',
      [
        "process.on('SIGTERM',()=>setTimeout(()=>process.exit(0),80))",
        "setTimeout(()=>process.stdout.write('x'.repeat(10000)),50)",
        'setInterval(()=>{},1000)',
      ].join(';'),
    ], {
      timeoutMs: 1_000,
      maxOutputBytes: 32,
    })).rejects.toMatchObject({ kind: 'output_limit' })

    expect(Date.now() - startedAt).toBeGreaterThanOrEqual(100)
  })

  it('runs a mutation once without forwarding caller cancellation', async () => {
    const calls: Array<{ readonly args: readonly string[]; readonly signal: unknown }> = []
    const runner: FileRunner = async (_file, args, options) => {
      calls.push({ args, signal: options.signal })
      return {
        exitCode: 1,
        stdout: '{"ok":false,"schema_version":"typed_failure"}',
        stderr: '/private/diagnostic',
      }
    }

    await expect(runJsonMutationCommand(command, ['mutate', '--execute'], {
      runner,
      timeoutMs: 50,
    })).rejects.toMatchObject({ kind: 'typed_failure', retryable: false })
    expect(calls).toEqual([{
      args: ['mutate', '--execute'],
      signal: undefined,
    }])
  })
})
