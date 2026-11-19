import { mkdir, writeFile } from 'node:fs/promises'
import { cpus, totalmem } from 'node:os'
import { dirname } from 'node:path'
import { pathToFileURL } from 'node:url'
import { runBaselineTrial, runObserverTrial, normalizeEventCount } from './overhead-runner.ts'
import type { TrialMeasurement } from './overhead-runner.ts'
import { buildOverheadReport, renderMarkdown } from './overhead-report.ts'

export interface CliOptions {
  readonly events: number
  readonly repeat: number
  readonly bufferBound: number
  readonly json: string | undefined
  readonly markdown: string | undefined
  readonly help: boolean
}

function positiveInteger(value: string | undefined, flag: string): number {
  const parsed = Number.parseInt(value ?? '', 10)
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`${flag} requires a positive integer`)
  }
  return parsed
}

export function parseArgs(args: readonly string[]): CliOptions {
  let events = 5_000
  let repeat = 3
  let bufferBound = 4_096
  let json: string | undefined
  let markdown: string | undefined
  let help = false
  for (let index = 0; index < args.length; index += 1) {
    const flag = args[index]
    switch (flag) {
      case '--help':
      case '-h':
        help = true
        break
      case '--events':
        index += 1
        events = positiveInteger(args[index], flag)
        break
      case '--repeat':
        index += 1
        repeat = positiveInteger(args[index], flag)
        break
      case '--buffer-bound':
        index += 1
        bufferBound = positiveInteger(args[index], flag)
        break
      case '--json':
        index += 1
        json = args[index]
        if (json === undefined) throw new Error('--json requires a path or "-"')
        break
      case '--markdown':
        index += 1
        markdown = args[index]
        if (markdown === undefined) throw new Error('--markdown requires a path or "-"')
        break
      default:
        throw new Error(`unknown argument: ${flag ?? ''}`)
    }
  }
  return { events, repeat, bufferBound, json, markdown, help }
}

function printUsage(): void {
  process.stdout.write(`Usage: node --experimental-strip-types bench/observer-overhead.ts [options]

Options:
  --events <n>        synthetic session events per trial, normalized down to a
                      whole 6-event turn cycle (default: 5000)
  --repeat <n>        measured trials per arm (default: 3)
  --buffer-bound <n>  observer buffer bound (default: 4096; pass 256 to mirror
                      the observer's product default and exercise drops)
  --json <path>       write the JSON report to path; "-" writes stdout
  --markdown <path>   write the Markdown report to path; "-" writes stdout
  --help              show this help
`)
}

async function main(): Promise<void> {
  const options = parseArgs(process.argv.slice(2))
  if (options.help) {
    printUsage()
    return
  }
  const events = normalizeEventCount(options.events)
  if (events !== options.events) {
    process.stderr.write(
      `note: normalized --events ${options.events} to ${events} (whole 6-event turn cycles)\n`,
    )
  }
  const warmupEvents = Math.min(600, events)
  runBaselineTrial({ inputEvents: warmupEvents, bufferBound: options.bufferBound })
  await runObserverTrial({ inputEvents: warmupEvents, bufferBound: options.bufferBound })

  const baseline: TrialMeasurement[] = []
  const observer: TrialMeasurement[] = []
  for (let index = 0; index < options.repeat; index += 1) {
    baseline.push(runBaselineTrial({ inputEvents: events, bufferBound: options.bufferBound }))
    observer.push(await runObserverTrial({ inputEvents: events, bufferBound: options.bufferBound }))
  }

  const report = buildOverheadReport({
    config: {
      events,
      requested_events: options.events,
      repeat: options.repeat,
      buffer_bound: options.bufferBound,
      event_mix: 'turn-cycle-v1',
    },
    baseline,
    observer,
    environment: {
      node: process.version,
      platform: process.platform,
      arch: process.arch,
      cpus: cpus().length,
      total_memory_gib: Number((totalmem() / 1_024 ** 3).toFixed(1)),
    },
  })
  const markdown = renderMarkdown(report)
  const json = `${JSON.stringify(report, null, 2)}\n`

  if (!report.usable) {
    process.stderr.write('warning: this report is not a usable overhead sample\n')
    for (const warning of report.warnings) process.stderr.write(`- ${warning}\n`)
  }

  let printed = false
  if (options.json === '-') {
    process.stdout.write(json)
    printed = true
  } else if (options.json !== undefined) {
    await mkdir(dirname(options.json), { recursive: true })
    await writeFile(options.json, json, 'utf8')
    process.stderr.write(`wrote ${options.json}\n`)
  }
  if (options.markdown === '-') {
    process.stdout.write(markdown)
    printed = true
  } else if (options.markdown !== undefined) {
    await mkdir(dirname(options.markdown), { recursive: true })
    await writeFile(options.markdown, markdown, 'utf8')
    process.stderr.write(`wrote ${options.markdown}\n`)
  }
  if (!printed && options.markdown === undefined) {
    process.stdout.write(markdown)
  }
}

if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    await main()
  } catch (error: unknown) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`)
    process.exitCode = 1
  }
}
