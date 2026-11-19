import type { TrialMeasurement } from './overhead-runner.ts'

export interface ArmSummary {
  readonly trials: number
  readonly usable_trials: number
  readonly excluded_trials: number
  readonly cpu_total_ms_median: number
  readonly cpu_total_ms_p95: number
  readonly cpu_ms_per_1k_events_median: number
  readonly rss_delta_bytes_median: number
  readonly ledger_bytes_per_event: number
  readonly stats_bytes_per_flush: number
  readonly append_p50_ms: number
  readonly append_p95_ms: number
  readonly persisted_events_median: number
}

export interface OverheadReportEnvironment {
  readonly node: string
  readonly platform: string
  readonly arch: string
  readonly cpus: number
  readonly total_memory_gib: number
}

export interface OverheadReportConfig {
  readonly events: number
  readonly requested_events?: number
  readonly repeat: number
  readonly buffer_bound: number
  readonly event_mix: string
}

export interface OverheadIntegrity {
  readonly count_conservation: boolean
  readonly no_event_loss: boolean
  readonly persisted_equals_accepted: boolean
  readonly all_trials_usable: boolean
  readonly observed_matches_plan: boolean
  readonly envelope_kind_mix_complete: boolean
  readonly outbound_endpoints_empty: boolean
  readonly worker_context_influence_absent: boolean
  readonly scheduler_input_influence_absent: boolean
  readonly observer_failures: number
  readonly persisted_envelopes: number
}

export interface OverheadEventAccounting {
  readonly observed_events_median: number
  readonly accepted_events_median: number
  readonly persisted_events_median: number
  readonly dropped_events_median: number
  readonly dropped_events_total: number
  readonly dropped_events_max: number
  readonly rejected_events_median: number
  readonly flush_attempts_median: number
  readonly peak_buffered_events_max: number
  readonly clock_uncertainty_max_ms_median: number
  readonly clock_harness_time_events_median: number
  readonly clock_wall_clock_events_median: number
  readonly trials_with_loss: number
  readonly usable_trials: number
  readonly excluded_trials: number
  readonly unconsumed_events_total: number
  readonly unsupported_envelopes_total: number
}

export interface OverheadDelta {
  readonly cpu_ms_per_1k_events: number
  readonly rss_delta_bytes: number
}

export interface OverheadReport {
  readonly schema: 'reliability_observer_overhead_report_v0'
  readonly generated_at: string
  readonly usable: boolean
  readonly warnings: readonly string[]
  readonly environment: OverheadReportEnvironment
  readonly config: OverheadReportConfig
  readonly arms: {
    readonly baseline: { readonly summary: ArmSummary; readonly trials: readonly TrialMeasurement[] }
    readonly observer: { readonly summary: ArmSummary; readonly trials: readonly TrialMeasurement[] }
  }
  readonly delta: OverheadDelta
  readonly integrity: OverheadIntegrity
  readonly event_accounting: OverheadEventAccounting
  readonly limits: readonly string[]
}

export interface OverheadReportInput {
  readonly config: OverheadReportConfig
  readonly baseline: readonly TrialMeasurement[]
  readonly observer: readonly TrialMeasurement[]
  readonly environment: OverheadReportEnvironment
  readonly generatedAt?: string
}

const REPORT_LIMITS = [
  'Synthetic event stream only; this is not a live DSH host, model, or network measurement.',
  'Measures observer component cost (event compaction, envelope construction, JSON serialization, and ledger append) through the shipped ledger writer; excludes Cordis hook dispatch, DSH scheduling, model, and network.',
  'Verifies that every planned record was observed and that the persisted envelope-kind mix matches the plan, so a retired or renamed planned event kind cannot read as a clean sample.',
  'The synthetic stream requests a flush at every turn end, but appends coalesce while one is in flight. The append-batch count, peak buffer, and per-event cost therefore depend on I/O latency; the report prints the actual counts and is not a workload forecast.',
  'Baseline is an empty synthetic driver; the delta is the observer component cost, not a claim about a real worker throughput.',
  'RSS is process-level and may move with GC; use repeats and medians, not a single sample.',
  'A trial that drops or rejects events, fails appends, loses persisted envelopes, or misses the planned record mix is excluded from the arm summaries; the report is marked not usable and prints warnings.',
  'A C1 eligibility or P0-exit claim requires a separately authorized live run and maintainer acceptance.',
] as const

export function percentile(values: readonly number[], fraction: number): number {
  if (values.length === 0) return 0
  const sorted = [...values].sort((left, right) => left - right)
  const bounded = Math.min(1, Math.max(0, fraction))
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil(bounded * sorted.length) - 1))
  return sorted[index] ?? 0
}

export function median(values: readonly number[]): number {
  return percentile(values, 0.5)
}

function maximum(values: readonly number[]): number {
  return values.length === 0 ? 0 : Math.max(...values)
}

interface EnvelopeKindDiff {
  readonly missing: readonly string[]
  readonly unexpected: readonly string[]
}

/** Kinds whose persisted count is below the plan, and kinds the plan lacks. */
export function envelopeKindDiff(trial: TrialMeasurement): EnvelopeKindDiff {
  const missing: string[] = []
  const unexpected: string[] = []
  for (const [kind, expected] of Object.entries(trial.expectedEnvelopeKinds)) {
    if ((trial.envelopeKinds[kind] ?? 0) < expected) missing.push(kind)
  }
  for (const [kind, actual] of Object.entries(trial.envelopeKinds)) {
    if (actual > (trial.expectedEnvelopeKinds[kind] ?? 0)) unexpected.push(kind)
  }
  return { missing, unexpected }
}

export function envelopeKindMixComplete(trial: TrialMeasurement): boolean {
  const diff = envelopeKindDiff(trial)
  return diff.missing.length === 0 && diff.unexpected.length === 0
}

/**
 * A trial whose timings can be compared with another trial: it lost no events,
 * rejected none, failed no append, persisted every accepted envelope, and
 * consumed the planned record mix. A trial that silently skips a planned event
 * kind (or turns it into an unknown kind) is not comparable either; the
 * baseline arm plans no envelopes, so those checks hold trivially for it.
 */
export function isUsableTrial(trial: TrialMeasurement): boolean {
  return trial.droppedEvents === 0
    && trial.rejectedEvents === 0
    && trial.observerFailures === 0
    && trial.persistedEvents === trial.acceptedEvents
    && trial.observedEvents === trial.expectedObservedEvents
    && envelopeKindMixComplete(trial)
}

export function summarizeTrials(trials: readonly TrialMeasurement[]): ArmSummary {
  const usable = trials.filter(isUsableTrial)
  const cpuPer1k = usable.map(trial => trial.workloadEvents === 0
    ? 0
    : (trial.cpuTotalMs / trial.workloadEvents) * 1_000)
  const appendLatencies = usable.flatMap(trial => trial.appendLatenciesMs)
  const totalEnvelopeBytes = usable.reduce((sum, trial) => sum + trial.envelopeBytes, 0)
  const totalStatsBytes = usable.reduce((sum, trial) => sum + trial.statsBytes, 0)
  const totalPersisted = usable.reduce((sum, trial) => sum + trial.persistedEvents, 0)
  const totalFlushes = usable.reduce((sum, trial) => sum + trial.flushAttempts, 0)
  return {
    trials: trials.length,
    usable_trials: usable.length,
    excluded_trials: trials.length - usable.length,
    cpu_total_ms_median: median(usable.map(trial => trial.cpuTotalMs)),
    cpu_total_ms_p95: percentile(usable.map(trial => trial.cpuTotalMs), 0.95),
    cpu_ms_per_1k_events_median: median(cpuPer1k),
    rss_delta_bytes_median: median(usable.map(trial => trial.rssDeltaBytes)),
    ledger_bytes_per_event: totalPersisted === 0 ? 0 : totalEnvelopeBytes / totalPersisted,
    stats_bytes_per_flush: totalFlushes === 0 ? 0 : totalStatsBytes / totalFlushes,
    append_p50_ms: percentile(appendLatencies, 0.5),
    append_p95_ms: percentile(appendLatencies, 0.95),
    persisted_events_median: median(usable.map(trial => trial.persistedEvents)),
  }
}

function buildWarnings(observer: readonly TrialMeasurement[]): readonly string[] {
  if (observer.length === 0) return ['No observer trial was measured.']
  const warnings: string[] = []
  const usableTrials = observer.filter(isUsableTrial).length
  if (usableTrials !== observer.length) {
    warnings.push(`Not a usable overhead sample: ${usableTrials} of ${observer.length} observer trials are usable.`)
  }
  const lossy = observer.filter(trial => trial.droppedEvents > 0)
  if (lossy.length > 0) {
    const dropped = lossy.map(trial => trial.droppedEvents)
    warnings.push(
      `${lossy.length} of ${observer.length} observer trials dropped events `
      + `(max ${maximum(dropped)}, total ${dropped.reduce((sum, value) => sum + value, 0)}); `
      + 'lossy trials are excluded from the arm summaries. Rerun with a larger --buffer-bound '
      + 'or fewer --events before treating this as an overhead sample.',
    )
  }
  const rejected = observer.reduce((sum, trial) => sum + trial.rejectedEvents, 0)
  if (rejected > 0) {
    warnings.push(`${rejected} observer event(s) were rejected; affected trials are excluded from the arm summaries.`)
  }
  const failures = observer.reduce((sum, trial) => sum + trial.observerFailures, 0)
  if (failures > 0) {
    warnings.push(`${failures} ledger append failure(s); affected trials are excluded from the arm summaries.`)
  }
  const mismatched = observer.filter(trial => trial.persistedEvents !== trial.acceptedEvents).length
  if (mismatched > 0) {
    warnings.push(
      `Persisted envelopes do not match accepted envelopes in ${mismatched} trial(s); `
      + 'a write may have been lost.',
    )
  }
  const unconsumed = observer.reduce(
    (sum, trial) => sum + Math.max(0, trial.expectedObservedEvents - trial.observedEvents), 0,
  )
  if (unconsumed > 0) {
    const affected = observer.filter(trial => trial.observedEvents !== trial.expectedObservedEvents).length
    warnings.push(
      `${affected} of ${observer.length} observer trial(s) did not observe every planned record `
      + `(${unconsumed} record(s) missing); a planned event kind may no longer be consumed. `
      + 'Update the bench event mix or the observer before treating this as an overhead sample.',
    )
  }
  const mixMismatches = observer.filter(trial => !envelopeKindMixComplete(trial))
  const firstMismatch = mixMismatches[0]
  if (firstMismatch !== undefined) {
    const diff = envelopeKindDiff(firstMismatch)
    warnings.push(
      `The persisted envelope kind mix does not match the plan in ${mixMismatches.length} of `
      + `${observer.length} observer trial(s); first mismatch is missing: `
      + `${diff.missing.length > 0 ? diff.missing.join(', ') : 'none'}, unexpected: `
      + `${diff.unexpected.length > 0 ? diff.unexpected.join(', ') : 'none'}. `
      + 'Such trials are excluded from the arm summaries.',
    )
  }
  return warnings
}

export function buildOverheadReport(input: OverheadReportInput): OverheadReport {
  const baselineSummary = summarizeTrials(input.baseline)
  const observerSummary = summarizeTrials(input.observer)
  const usable = input.observer.length > 0 && input.observer.every(isUsableTrial)
  const dropped = input.observer.map(trial => trial.droppedEvents)
  return {
    schema: 'reliability_observer_overhead_report_v0',
    generated_at: input.generatedAt ?? new Date().toISOString(),
    usable,
    warnings: buildWarnings(input.observer),
    environment: input.environment,
    config: input.config,
    arms: {
      baseline: { summary: baselineSummary, trials: input.baseline },
      observer: { summary: observerSummary, trials: input.observer },
    },
    delta: {
      cpu_ms_per_1k_events: observerSummary.cpu_ms_per_1k_events_median
        - baselineSummary.cpu_ms_per_1k_events_median,
      rss_delta_bytes: observerSummary.rss_delta_bytes_median
        - baselineSummary.rss_delta_bytes_median,
    },
    integrity: {
      count_conservation: input.observer.every(trial => trial.observedEvents
        === trial.acceptedEvents + trial.rejectedEvents + trial.droppedEvents),
      no_event_loss: input.observer.every(trial => trial.droppedEvents === 0),
      persisted_equals_accepted: input.observer.every(trial => trial.persistedEvents === trial.acceptedEvents),
      all_trials_usable: usable,
      observed_matches_plan: input.observer.every(trial => trial.observedEvents === trial.expectedObservedEvents),
      envelope_kind_mix_complete: input.observer.every(envelopeKindMixComplete),
      outbound_endpoints_empty: input.observer.every(trial => trial.outboundEndpoints === 0),
      worker_context_influence_absent: input.observer.every(trial => !trial.workerContextInfluence),
      scheduler_input_influence_absent: input.observer.every(trial => !trial.schedulerInputInfluence),
      observer_failures: input.observer.reduce((sum, trial) => sum + trial.observerFailures, 0),
      persisted_envelopes: input.observer.reduce((sum, trial) => sum + trial.persistedEvents, 0),
    },
    event_accounting: {
      observed_events_median: median(input.observer.map(trial => trial.observedEvents)),
      accepted_events_median: median(input.observer.map(trial => trial.acceptedEvents)),
      persisted_events_median: median(input.observer.map(trial => trial.persistedEvents)),
      dropped_events_median: median(dropped),
      dropped_events_total: dropped.reduce((sum, value) => sum + value, 0),
      dropped_events_max: maximum(dropped),
      rejected_events_median: median(input.observer.map(trial => trial.rejectedEvents)),
      flush_attempts_median: median(input.observer.map(trial => trial.flushAttempts)),
      peak_buffered_events_max: maximum(input.observer.map(trial => trial.peakBufferedEvents)),
      clock_uncertainty_max_ms_median: median(input.observer.map(trial => trial.clockUncertaintyMaxMs)),
      clock_harness_time_events_median: median(input.observer.map(trial => trial.harnessTimeEvents)),
      clock_wall_clock_events_median: median(input.observer.map(trial => trial.wallClockEvents)),
      trials_with_loss: input.observer.filter(trial => trial.droppedEvents > 0).length,
      usable_trials: observerSummary.usable_trials,
      excluded_trials: observerSummary.excluded_trials,
      unconsumed_events_total: input.observer.reduce(
        (sum, trial) => sum + Math.max(0, trial.expectedObservedEvents - trial.observedEvents), 0,
      ),
      unsupported_envelopes_total: input.observer.reduce(
        (sum, trial) => sum + (trial.envelopeKinds.unsupported ?? 0), 0,
      ),
    },
    limits: REPORT_LIMITS,
  }
}

function formatNumber(value: number, digits = 2): string {
  return Number.isFinite(value) ? value.toFixed(digits) : 'n/a'
}

function formatInteger(value: number): string {
  return Number.isFinite(value) ? Math.round(value).toLocaleString('en-US') : 'n/a'
}

function formatBytes(value: number): string {
  const sign = value < 0 ? '-' : ''
  const absolute = Math.abs(value)
  if (absolute < 1_024) return `${sign}${absolute.toFixed(0)} B`
  if (absolute < 1_024 ** 2) return `${sign}${(absolute / 1_024).toFixed(1)} KiB`
  return `${sign}${(absolute / 1_024 ** 2).toFixed(1)} MiB`
}

function status(value: boolean): string {
  return value ? 'passed' : 'failed'
}

export function renderMarkdown(report: OverheadReport): string {
  const baseline = report.arms.baseline.summary
  const observer = report.arms.observer.summary
  const requested = report.config.requested_events
  const eventsLine = requested !== undefined && requested !== report.config.events
    ? `${formatInteger(report.config.events)} (requested ${formatInteger(requested)}; `
      + 'normalized to whole 6-event turn cycles)'
    : formatInteger(report.config.events)
  const warnings = report.warnings.length === 0
    ? ''
    : `\n## Warnings\n\n${report.warnings.map(warning => `- ${warning}`).join('\n')}\n`
  const baselineTiming = (value: number, digits = 2): string =>
    baseline.usable_trials === 0 ? 'n/a' : formatNumber(value, digits)
  const observerTiming = (value: number, digits = 2): string =>
    observer.usable_trials === 0 ? 'n/a' : formatNumber(value, digits)
  const baselineBytes = (value: number): string =>
    baseline.usable_trials === 0 ? 'n/a' : formatBytes(value)
  const observerBytes = (value: number): string =>
    observer.usable_trials === 0 ? 'n/a' : formatBytes(value)
  const observerInteger = (value: number): string =>
    observer.usable_trials === 0 ? 'n/a' : formatInteger(value)
  return `# Observer overhead (synthetic DSH session)

- Status: **${report.usable ? 'usable' : 'NOT USABLE'}**
- Generated: ${report.generated_at}
- Node: ${report.environment.node}
- Platform: ${report.environment.platform}/${report.environment.arch}
- CPUs: ${report.environment.cpus}
- Memory: ${formatNumber(report.environment.total_memory_gib, 1)} GiB
- Events per trial: ${eventsLine}
- Repeats per arm: ${formatInteger(report.config.repeat)}
- Observer trials used for timing: ${formatInteger(observer.usable_trials)} of ${formatInteger(observer.trials)}
- Buffer bound: ${formatInteger(report.config.buffer_bound)}
- Event mix: ${report.config.event_mix}

| Metric | Baseline | Observer | Delta |
| --- | ---: | ---: | ---: |
| CPU total, median (ms) | ${baselineTiming(baseline.cpu_total_ms_median)} | ${observerTiming(observer.cpu_total_ms_median)} | ${observerTiming(observer.cpu_total_ms_median - baseline.cpu_total_ms_median)} |
| CPU, median (ms / 1k events) | ${baselineTiming(baseline.cpu_ms_per_1k_events_median)} | ${observerTiming(observer.cpu_ms_per_1k_events_median)} | ${observerTiming(report.delta.cpu_ms_per_1k_events)} |
| RSS delta, median | ${baselineBytes(baseline.rss_delta_bytes_median)} | ${observerBytes(observer.rss_delta_bytes_median)} | ${observerBytes(report.delta.rss_delta_bytes)} |
| Ledger append p50 (ms) | n/a | ${observerTiming(observer.append_p50_ms)} | n/a |
| Ledger append p95 (ms) | n/a | ${observerTiming(observer.append_p95_ms)} | n/a |
| Ledger bytes / envelope | n/a | ${observerTiming(observer.ledger_bytes_per_event)} | n/a |
| Ledger stats bytes / flush | n/a | ${observerTiming(observer.stats_bytes_per_flush)} | n/a |
| Persisted envelopes, median | n/a | ${observerInteger(observer.persisted_events_median)} | n/a |
| Clock source, median (harness / wall-clock) | n/a | ${formatInteger(report.event_accounting.clock_harness_time_events_median)} / ${formatInteger(report.event_accounting.clock_wall_clock_events_median)} | n/a |
| Clock uncertainty max, median (ms) | n/a | ${formatNumber(report.event_accounting.clock_uncertainty_max_ms_median)} | n/a |

## Event accounting (observer, all trials)

| Observed | Accepted | Persisted | Dropped (total) | Dropped (max) | Rejected | Flush attempts | Peak buffer (max) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ${formatInteger(report.event_accounting.observed_events_median)} | ${formatInteger(report.event_accounting.accepted_events_median)} | ${formatInteger(report.event_accounting.persisted_events_median)} | ${formatInteger(report.event_accounting.dropped_events_total)} | ${formatInteger(report.event_accounting.dropped_events_max)} | ${formatInteger(report.event_accounting.rejected_events_median)} | ${formatInteger(report.event_accounting.flush_attempts_median)} | ${formatInteger(report.event_accounting.peak_buffered_events_max)} |

- Trials with loss: ${formatInteger(report.event_accounting.trials_with_loss)} of ${formatInteger(observer.trials)}
- Trials used for timing: ${formatInteger(report.event_accounting.usable_trials)} of ${formatInteger(observer.trials)} (${formatInteger(report.event_accounting.excluded_trials)} excluded)
- Planned records not observed: ${formatInteger(report.event_accounting.unconsumed_events_total)}
- Unsupported envelopes: ${formatInteger(report.event_accounting.unsupported_envelopes_total)}

## Integrity

- No event loss: ${status(report.integrity.no_event_loss)}
- Persisted equals accepted: ${status(report.integrity.persisted_equals_accepted)}
- All trials usable: ${status(report.integrity.all_trials_usable)}
- Observed matches plan: ${status(report.integrity.observed_matches_plan)}
- Planned envelope mix complete: ${status(report.integrity.envelope_kind_mix_complete)}
- Count conservation: ${status(report.integrity.count_conservation)}
- Outbound endpoints empty: ${status(report.integrity.outbound_endpoints_empty)}
- Worker-context influence absent: ${status(report.integrity.worker_context_influence_absent)}
- Scheduler-input influence absent: ${status(report.integrity.scheduler_input_influence_absent)}
- Observer failures: ${formatInteger(report.integrity.observer_failures)}
- Persisted envelopes: ${formatInteger(report.integrity.persisted_envelopes)}
${warnings}
## Limits

${report.limits.map(limit => `- ${limit}`).join('\n')}
`
}
