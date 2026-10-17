import { stat } from 'node:fs/promises'
import { readdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { delimiter, join, resolve } from 'node:path'
import { resolveLoopXCommand, runFile } from './cli.ts'
import type { FileRunner, LoopXCommand } from './cli.ts'

export const MANAGED_LAUNCHER_NAME = 'loopx_cli.py'
export const MANAGED_SITE_PACKAGES_NAME = 'site-packages'

export interface LoopXRuntimeOptions {
  readonly runner?: FileRunner | undefined
  readonly signal?: AbortSignal | undefined
  readonly env?: NodeJS.ProcessEnv | undefined
  readonly runtimeDir?: string | undefined
  readonly pythonBin?: string | undefined
}

export function configuredPluginPython(
  options: LoopXRuntimeOptions,
): string | undefined {
  return options.pythonBin
    ?? options.env?.PYTHON_BIN
    ?? process.env.PYTHON_BIN
}

export function pluginPythonCandidates(
  options: LoopXRuntimeOptions,
): readonly string[] {
  const explicit = configuredPluginPython(options)
  if (explicit !== undefined) return [explicit]
  const env = options.env ?? process.env
  const searchPath = env.PATH ?? env.Path
  const discovered = new Map<string, number>()
  for (const directory of searchPath?.split(delimiter) ?? []) {
    try {
      for (const entry of readdirSync(directory || '.', { withFileTypes: true })) {
        const match = /^python3\.(\d+)(?:\.exe)?$/i.exec(entry.name)
        if (match?.[1] !== undefined && !entry.isDirectory()) {
          discovered.set(entry.name, Number(match[1]))
        }
      }
    } catch {
      // Missing or unreadable PATH entries are not interpreter candidates.
    }
  }
  // Preserve python3-first behavior; callers still probe the actual version/pip.
  // `python` is a bare last-resort name, not a PATH scan result: a python.org
  // Windows install registers `python.exe`, and the host's `python3` name can be
  // the WindowsApps alias that never starts Python. The same fallback pair is
  // used by the Claude Code adapter installer
  // (loopx/claude_goal_mode/scripts/install.py), and the callers above still
  // reject an interpreter that fails the version/pip probe.
  const discoveredNames = [...discovered]
    .sort(([nameA, minorA], [nameB, minorB]) => minorB - minorA || nameA.localeCompare(nameB))
    .map(([name]) => name)
  return ['python3', ...discoveredNames, 'python']
}

export function pluginAgentsHome(options: LoopXRuntimeOptions): string {
  const configured = options.env?.DSH_AGENTS_HOME
    ?? process.env.DSH_AGENTS_HOME
  return configured?.trim() ? configured : join(homedir(), '.agents')
}

export function pluginRuntimeDir(options: LoopXRuntimeOptions): string {
  return resolve(
    options.runtimeDir
      ?? join(pluginAgentsHome(options), 'runtime', 'dsh-loopx-plugin'),
  )
}

/** Resolve the exact CLI surface shared by bootstrap, Driver, and GoalBar. */
export async function resolvePluginLoopXCommand(
  options: LoopXRuntimeOptions = {},
): Promise<LoopXCommand> {
  const launcherPath = join(pluginRuntimeDir(options), MANAGED_LAUNCHER_NAME)
  const env = options.pythonBin === undefined
    ? options.env
    : { ...(options.env ?? process.env), PYTHON_BIN: options.pythonBin }
  const hasManagedLauncher = await stat(launcherPath).then(
    value => value.isFile(),
    () => false,
  )
  return resolveLoopXCommand({
    ...options,
    runner: options.runner ?? runFile,
    env,
    managedLauncher: hasManagedLauncher
      ? { path: launcherPath, pythonBins: pluginPythonCandidates(options) }
      : undefined,
  })
}
