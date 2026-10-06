import { spawn } from 'node:child_process'

// 1.2.4 includes the released Windows peer-file fix. Bootstrap and every
// runtime consumer must use the same floor, including an existing global CLI.
export const MINIMUM_LOOPX_VERSION = '1.2.4'

export type LoopXCliErrorKind =
  | 'aborted'
  | 'missing'
  | 'timeout'
  | 'transport'
  | 'output_limit'
  | 'exit'
  | 'typed_failure'
  | 'invalid_json'
  | 'invalid_schema'

export class LoopXCliError extends Error {
  constructor(
    readonly kind: LoopXCliErrorKind,
    message: string,
    readonly retryable: boolean,
    readonly exitCode?: number | undefined,
  ) {
    super(message)
    this.name = 'LoopXCliError'
  }
}

export interface FileResult {
  readonly exitCode: number
  readonly stdout: string
  readonly stderr: string
}

export interface FileRunOptions {
  readonly cwd?: string | undefined
  readonly env?: NodeJS.ProcessEnv | undefined
  readonly signal?: AbortSignal | undefined
  readonly timeoutMs?: number | undefined
  readonly maxOutputBytes?: number | undefined
}

export type FileRunner = (
  file: string,
  args: readonly string[],
  options: FileRunOptions,
) => Promise<FileResult>

export interface LoopXCommand {
  readonly file: string
  readonly prefix: readonly string[]
  readonly skillCommand: string
  readonly version: string
}

export interface ManagedLoopXLauncher {
  readonly path: string
  readonly pythonBins: readonly string[]
}

export interface ResolveLoopXCommandOptions {
  readonly runner?: FileRunner | undefined
  readonly signal?: AbortSignal | undefined
  readonly env?: NodeJS.ProcessEnv | undefined
  readonly managedLauncher?: ManagedLoopXLauncher | undefined
}

const DEFAULT_TIMEOUT_MS = 20_000
const DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024
const FORCE_KILL_GRACE_MS = 250

function transportKind(error: NodeJS.ErrnoException): LoopXCliError {
  if (error.code === 'ENOENT') {
    return new LoopXCliError('missing', 'required executable is unavailable', false)
  }
  return new LoopXCliError('transport', 'could not start the LoopX command', true)
}

export const runFile: FileRunner = (file, args, options) => new Promise((resolve, reject) => {
  if (options.signal?.aborted) {
    reject(new LoopXCliError('aborted', 'operation was cancelled', false))
    return
  }

  const child = spawn(file, [...args], {
    cwd: options.cwd,
    env: options.env ?? process.env,
    shell: false,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  const stdout: Buffer[] = []
  const stderr: Buffer[] = []
  const limit = options.maxOutputBytes ?? DEFAULT_MAX_OUTPUT_BYTES
  let outputBytes = 0
  let settled = false
  let terminalError: LoopXCliError | undefined
  let forceKillTimer: ReturnType<typeof setTimeout> | undefined

  const cleanup = (): void => {
    clearTimeout(timer)
    if (forceKillTimer !== undefined) clearTimeout(forceKillTimer)
    options.signal?.removeEventListener('abort', abort)
  }
  const fail = (error: LoopXCliError): void => {
    if (settled || terminalError !== undefined) return
    terminalError = error
    clearTimeout(timer)
    options.signal?.removeEventListener('abort', abort)
    child.kill('SIGTERM')
    forceKillTimer = setTimeout(() => {
      if (!settled) child.kill('SIGKILL')
    }, FORCE_KILL_GRACE_MS)
  }
  const collect = (target: Buffer[], chunk: Buffer): void => {
    if (terminalError !== undefined) return
    outputBytes += chunk.byteLength
    if (outputBytes > limit) {
      fail(new LoopXCliError('output_limit', 'LoopX output exceeded its bounded limit', false))
      return
    }
    target.push(chunk)
  }
  const abort = (): void => {
    fail(new LoopXCliError('aborted', 'operation was cancelled', false))
  }
  const timer = setTimeout(() => {
    fail(new LoopXCliError('timeout', 'LoopX command timed out', true))
  }, options.timeoutMs ?? DEFAULT_TIMEOUT_MS)

  options.signal?.addEventListener('abort', abort, { once: true })
  child.stdout.on('data', (chunk: Buffer) => collect(stdout, chunk))
  child.stderr.on('data', (chunk: Buffer) => collect(stderr, chunk))
  child.on('error', (error: NodeJS.ErrnoException) => fail(transportKind(error)))
  child.on('close', code => {
    if (settled) return
    settled = true
    cleanup()
    if (terminalError !== undefined) {
      reject(terminalError)
      return
    }
    resolve({
      exitCode: code ?? 1,
      stdout: Buffer.concat(stdout).toString('utf8'),
      stderr: Buffer.concat(stderr).toString('utf8'),
    })
  })
})

function recordPayload(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined
}

function parsePayload(stdout: string): Record<string, unknown> | undefined {
  try {
    return recordPayload(JSON.parse(stdout))
  } catch {
    return undefined
  }
}

async function wait(delayMs: number, signal?: AbortSignal): Promise<void> {
  if (delayMs <= 0) return
  await new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new LoopXCliError('aborted', 'operation was cancelled', false))
      return
    }
    const timer = setTimeout(done, delayMs)
    function done(): void {
      signal?.removeEventListener('abort', aborted)
      resolve()
    }
    function aborted(): void {
      clearTimeout(timer)
      signal?.removeEventListener('abort', aborted)
      reject(new LoopXCliError('aborted', 'operation was cancelled', false))
    }
    signal?.addEventListener('abort', aborted, { once: true })
  })
}

export interface JsonCommandOptions extends FileRunOptions {
  readonly runner?: FileRunner | undefined
  readonly attempts?: number | undefined
  readonly retryDelaysMs?: readonly number[] | undefined
  readonly validate?: ((payload: Record<string, unknown>) => boolean) | undefined
}

export type JsonMutationCommandOptions = Omit<
  JsonCommandOptions,
  'attempts' | 'retryDelaysMs' | 'signal'
>

/**
 * Execute one mutation exactly once. Caller cancellation is intentionally not
 * accepted: admission is fenced before this function is called, and the real
 * runner waits for child close after every forced termination.
 */
export async function runJsonMutationCommand(
  command: LoopXCommand,
  args: readonly string[],
  options: JsonMutationCommandOptions = {},
): Promise<Record<string, unknown>> {
  const runner = options.runner ?? runFile
  let result: FileResult
  try {
    result = await runner(
      command.file,
      [...command.prefix, ...args],
      {
        cwd: options.cwd,
        env: options.env,
        timeoutMs: options.timeoutMs,
        maxOutputBytes: options.maxOutputBytes,
      },
    )
  } catch (error: unknown) {
    throw error instanceof LoopXCliError
      ? error
      : new LoopXCliError(
          'transport',
          'LoopX mutation command failed to execute',
          false,
        )
  }

  const payload = parsePayload(result.stdout)
  if (result.exitCode !== 0) {
    throw new LoopXCliError(
      payload === undefined ? 'exit' : 'typed_failure',
      'LoopX mutation command did not verify',
      false,
      result.exitCode,
    )
  }
  if (payload === undefined) {
    throw new LoopXCliError(
      'invalid_json',
      'LoopX mutation command returned invalid JSON',
      false,
    )
  }
  if (options.validate !== undefined && !options.validate(payload)) {
    throw new LoopXCliError(
      'invalid_schema',
      'LoopX mutation command returned an incompatible response schema',
      false,
    )
  }
  return payload
}

/** Execute fixed argv and retry only transport/timeout/untyped-exit failures. */
export async function runJsonCommand(
  command: LoopXCommand,
  args: readonly string[],
  options: JsonCommandOptions = {},
): Promise<Record<string, unknown>> {
  const runner = options.runner ?? runFile
  const attempts = Math.max(1, options.attempts ?? 1)
  const retryDelays = options.retryDelaysMs ?? [200, 750]
  let lastError: LoopXCliError | undefined

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const result = await runner(
        command.file,
        [...command.prefix, ...args],
        options,
      )
      const payload = parsePayload(result.stdout)
      if (payload !== undefined) {
        if (options.validate !== undefined && !options.validate(payload)) {
          throw new LoopXCliError(
            'invalid_schema',
            'LoopX returned an incompatible response schema',
            false,
          )
        }
        return payload
      }
      if (result.exitCode === 0) {
        throw new LoopXCliError('invalid_json', 'LoopX returned invalid JSON', false)
      }
      throw new LoopXCliError(
        'exit',
        'LoopX exited without a typed response',
        true,
        result.exitCode,
      )
    } catch (error: unknown) {
      const failure = error instanceof LoopXCliError
        ? error
        : new LoopXCliError('transport', 'LoopX command failed to execute', true)
      lastError = failure
      if (!failure.retryable || attempt + 1 >= attempts) throw failure
      await wait(retryDelays[attempt] ?? retryDelays.at(-1) ?? 0, options.signal)
    }
  }
  throw lastError ?? new LoopXCliError('transport', 'LoopX command failed', true)
}

function versionText(stdout: string): string | undefined {
  const value = stdout.trim().split(/\r?\n/u)[0]?.trim()
  if (!value || value.length > 120) return undefined
  // LoopX reports its Python package version. Compare the release tuple and
  // recognize its PEP 440 pre/dev/post/local suffixes without accepting prose.
  const parsed = /^loopx (\d+)\.(\d+)\.(\d+)((?:a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?(?:\+[A-Za-z0-9.-]+)?$/u.exec(value)
  if (!parsed) return undefined
  const release = parsed.slice(1, 4).map(Number)
  if (release.some(component => !Number.isSafeInteger(component))) return undefined
  const floor = MINIMUM_LOOPX_VERSION.split('.').map(Number)
  for (let index = 0; index < floor.length; index += 1) {
    const component = release[index]!
    if (component < floor[index]!) return undefined
    if (component > floor[index]!) return value
  }
  return !parsed[4] && (!parsed[6] || parsed[5]) ? value : undefined
}

function shellWord(value: string): string {
  return /^[A-Za-z0-9_./:-]+$/u.test(value)
    ? value
    : `'${value.replaceAll("'", `'"'"'`)}'`
}

/** Resolve the console script first, then the stable Python module fallback. */
export async function resolveLoopXCommand(
  options: ResolveLoopXCommandOptions = {},
): Promise<LoopXCommand> {
  const runner = options.runner ?? runFile
  const configured = options.env?.LOOPX_BIN ?? process.env.LOOPX_BIN
  const python = options.env?.PYTHON_BIN ?? process.env.PYTHON_BIN ?? 'python3'
  const managedLauncher = options.managedLauncher
  const managedCandidates: Array<Omit<LoopXCommand, 'version'>> = configured
    || managedLauncher === undefined
    ? []
    : [...new Set(managedLauncher.pythonBins)].map(pythonBin => ({
        file: pythonBin,
        prefix: [managedLauncher.path],
        skillCommand: [
          shellWord(pythonBin),
          shellWord(managedLauncher.path),
        ].join(' '),
      }))
  const candidates: Array<Omit<LoopXCommand, 'version'>> = configured
    ? [{ file: configured, prefix: [], skillCommand: '"$LOOPX_BIN"' }]
    : [
        ...managedCandidates,
        { file: 'loopx', prefix: [], skillCommand: 'loopx' },
        {
          file: python,
          prefix: ['-m', 'loopx.cli'],
          skillCommand: `${shellWord(python)} -m loopx.cli`,
        },
      ]

  for (const candidate of candidates) {
    try {
      const result = await runner(
        candidate.file,
        [...candidate.prefix, '--version'],
        {
          env: options.env,
          signal: options.signal,
          timeoutMs: 5_000,
          maxOutputBytes: 16 * 1024,
        },
      )
      const version = result.exitCode === 0 ? versionText(result.stdout) : undefined
      if (version) return { ...candidate, version }
    } catch (error: unknown) {
      if (error instanceof LoopXCliError && error.kind === 'aborted') throw error
    }
  }
  throw new LoopXCliError('missing', `LoopX CLI ${MINIMUM_LOOPX_VERSION} or newer is unavailable`, false)
}
