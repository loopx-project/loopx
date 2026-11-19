import { describe, expect, it } from 'vitest'
import {
  buildEventPlan,
  normalizeEventCount,
  runBaselineTrial,
  runObserverTrial,
} from '../bench/overhead-runner.ts'
import type { PlannedEvent } from '../bench/overhead-runner.ts'
import {
  buildOverheadReport,
  isUsableTrial,
  percentile,
  renderMarkdown,
  summarizeTrials,
} from '../bench/overhead-report.ts'
import { parseArgs } from '../bench/observer-overhead.ts'

const environment = {
  node: 'v-test',
  platform: 'test',
  arch: 'test',
  cpus: 1,
  total_memory_gib: 1,
}

describe('observer overhead event plan', () => {
  it('normalizes to whole turn cycles and pins the stream shape', () => {
    expect(normalizeEventCount(0)).toBe(6)
    expect(normalizeEventCount(10)).toBe(6)
    expect(normalizeEventCount(13)).toBe(12)
    const plan = buildEventPlan(12)
    expect(plan).toHaveLength(14)
    expect(plan[0]?.kind).toBe('created')
    expect(plan.at(-1)?.kind).toBe('disposed')
    const eventTypes = plan
      .filter((planned): planned is Extract<PlannedEvent, { kind: 'event' }> => planned.kind === 'event')
      .map(planned => planned.event.type)
    expect(eventTypes).toEqual([
      'turn/start', 'step/start', 'tool/call', 'tool/result', 'step/end', 'turn/end',
      'turn/start', 'step/start', 'tool/call', 'tool/result', 'step/end', 'turn/end',
    ])
  })

  it('keeps percentile boundaries deterministic', () => {
    expect(percentile([], 0.5)).toBe(0)
    expect(percentile([1, 2, 3, 4], 0.5)).toBe(2)
    expect(percentile([1, 2, 3, 4], 0.95)).toBe(4)
    expect(percentile([4, 1, 3, 2], 0.5)).toBe(2)
  })
})

describe('observer overhead trials', () => {
  it('measures observer component cost with full denominators and a clock mix', async () => {
    const trial = await runObserverTrial({ inputEvents: 60, bufferBound: 256 })
    expect(trial.workloadEvents).toBe(60)
    expect(trial.observedEvents).toBe(62)
    expect(trial.expectedObservedEvents).toBe(62)
    expect(trial.envelopeKinds).toEqual({
      session_started: 1,
      session_disposed: 1,
      turn_started: 10,
      step_started: 10,
      tool_called: 10,
      tool_completed: 10,
      step_ended: 10,
      turn_ended: 10,
    })
    expect(trial.observedEvents).toBe(
      trial.acceptedEvents + trial.rejectedEvents + trial.droppedEvents,
    )
    expect(trial.persistedEvents).toBe(trial.acceptedEvents)
    expect(trial.persistedEvents).toBeGreaterThan(0)
    expect(trial.ledgerBytes).toBe(trial.envelopeBytes + trial.statsBytes)
    expect(trial.envelopeBytes).toBeGreaterThan(0)
    expect(trial.statsBytes).toBeGreaterThan(0)
    expect(trial.clockUncertaintyMaxMs).toBe(50)
    expect(trial.harnessTimeEvents).toBe(60)
    expect(trial.wallClockEvents).toBe(2)
    expect(trial.appendLatenciesMs.length).toBeGreaterThan(0)
    expect(trial.outboundEndpoints).toBe(0)
    expect(trial.workerContextInfluence).toBe(false)
    expect(trial.schedulerInputInfluence).toBe(false)
    expect(isUsableTrial(trial)).toBe(true)
  })

  it('counts backpressure loss as an unusable trial', async () => {
    const trial = await runObserverTrial({ inputEvents: 60, bufferBound: 4 })
    expect(trial.droppedEvents).toBeGreaterThan(0)
    expect(trial.observedEvents).toBe(
      trial.acceptedEvents + trial.rejectedEvents + trial.droppedEvents,
    )
    expect(trial.persistedEvents).toBe(trial.acceptedEvents)
    expect(isUsableTrial(trial)).toBe(false)
  })

  it('counts append failures without throwing or persisting controls', async () => {
    const trial = await runObserverTrial({
      inputEvents: 12,
      bufferBound: 4,
      failAppend: true,
    })
    expect(trial.observerFailures).toBeGreaterThan(0)
    expect(trial.persistedEvents).toBe(0)
    expect(trial.observedEvents).toBe(
      trial.acceptedEvents + trial.rejectedEvents + trial.droppedEvents,
    )
    expect(trial.outboundEndpoints).toBe(0)
    expect(isUsableTrial(trial)).toBe(false)
  })

  it('renders a public-safe usable report with both arms and integrity status', async () => {
    const baseline = [runBaselineTrial({ inputEvents: 12, bufferBound: 256 })]
    const observer = [await runObserverTrial({ inputEvents: 12, bufferBound: 256 })]
    expect(summarizeTrials(baseline).trials).toBe(1)
    expect(summarizeTrials(baseline).usable_trials).toBe(1)
    expect(summarizeTrials(observer).trials).toBe(1)
    expect(summarizeTrials(observer).usable_trials).toBe(1)
    const report = buildOverheadReport({
      config: {
        events: 12,
        requested_events: 13,
        repeat: 1,
        buffer_bound: 256,
        event_mix: 'turn-cycle-v1',
      },
      baseline,
      observer,
      environment,
      generatedAt: '2026-10-19T00:00:00.000Z',
    })
    expect(report.schema).toBe('reliability_observer_overhead_report_v0')
    expect(report.usable).toBe(true)
    expect(report.warnings).toEqual([])
    expect(report.integrity.no_event_loss).toBe(true)
    expect(report.integrity.persisted_equals_accepted).toBe(true)
    expect(report.integrity.all_trials_usable).toBe(true)
    expect(report.integrity.observed_matches_plan).toBe(true)
    expect(report.integrity.envelope_kind_mix_complete).toBe(true)
    expect(report.integrity.count_conservation).toBe(true)
    expect(report.integrity.outbound_endpoints_empty).toBe(true)
    expect(report.integrity.worker_context_influence_absent).toBe(true)
    expect(report.integrity.scheduler_input_influence_absent).toBe(true)
    expect(report.event_accounting.observed_events_median).toBe(14)
    expect(report.event_accounting.dropped_events_total).toBe(0)
    expect(report.event_accounting.dropped_events_max).toBe(0)
    expect(report.event_accounting.trials_with_loss).toBe(0)
    expect(report.event_accounting.usable_trials).toBe(1)
    expect(report.event_accounting.peak_buffered_events_max).toBeGreaterThan(0)
    expect(report.event_accounting.clock_uncertainty_max_ms_median).toBe(50)
    expect(report.event_accounting.clock_harness_time_events_median).toBe(12)
    expect(report.event_accounting.clock_wall_clock_events_median).toBe(2)
    expect(report.arms.observer.summary.ledger_bytes_per_event).toBeGreaterThan(0)
    expect(report.arms.observer.summary.stats_bytes_per_flush).toBeGreaterThan(0)
    expect(Number.isFinite(report.delta.cpu_ms_per_1k_events)).toBe(true)
    expect(Number.isFinite(report.delta.rss_delta_bytes)).toBe(true)
    const markdown = renderMarkdown(report)
    expect(markdown).toContain('Observer overhead')
    expect(markdown).toContain('Status: **usable**')
    expect(markdown).toContain('No event loss: passed')
    expect(markdown).toContain('Persisted equals accepted: passed')
    expect(markdown).toContain('Events per trial: 12 (requested 13; normalized to whole 6-event turn cycles)')
    expect(markdown).not.toContain('## Warnings')
    expect(markdown).not.toContain('session-observer-overhead-fixture')
    expect(markdown).not.toContain('goal-observer-overhead-fixture')
    expect(JSON.stringify(report)).not.toContain('session-observer-overhead-fixture')
    expect(JSON.stringify(report)).not.toContain('goal-observer-overhead-fixture')
    expect(JSON.stringify(report)).not.toContain('loopx-observer-overhead-')
  })

  it('marks a lossy run unusable instead of hiding loss behind medians', async () => {
    const baseline = [runBaselineTrial({ inputEvents: 60, bufferBound: 4 })]
    const lossy = await runObserverTrial({ inputEvents: 60, bufferBound: 4 })
    const clean = await runObserverTrial({ inputEvents: 60, bufferBound: 256 })
    expect(lossy.droppedEvents).toBeGreaterThan(0)
    expect(clean.droppedEvents).toBe(0)
    const report = buildOverheadReport({
      config: { events: 60, repeat: 2, buffer_bound: 4, event_mix: 'turn-cycle-v1' },
      baseline,
      observer: [lossy, clean],
      environment,
      generatedAt: '2026-10-19T00:00:00.000Z',
    })
    expect(report.usable).toBe(false)
    expect(report.integrity.no_event_loss).toBe(false)
    expect(report.integrity.all_trials_usable).toBe(false)
    expect(report.event_accounting.trials_with_loss).toBe(1)
    expect(report.event_accounting.dropped_events_total).toBe(lossy.droppedEvents)
    expect(report.event_accounting.dropped_events_max).toBe(lossy.droppedEvents)
    expect(report.arms.observer.summary.trials).toBe(2)
    expect(report.arms.observer.summary.usable_trials).toBe(1)
    expect(report.arms.observer.summary.excluded_trials).toBe(1)
    expect(report.arms.observer.summary.persisted_events_median).toBe(clean.persistedEvents)
    expect(report.warnings.length).toBeGreaterThan(0)
    const markdown = renderMarkdown(report)
    expect(markdown).toContain('Status: **NOT USABLE**')
    expect(markdown).toContain('No event loss: failed')
    expect(markdown).toContain('Trials with loss: 1 of 2')
    expect(markdown).toContain('## Warnings')
  })

  it('marks an append-failed run unusable without persisting', async () => {
    const trial = await runObserverTrial({
      inputEvents: 12,
      bufferBound: 4,
      failAppend: true,
    })
    const report = buildOverheadReport({
      config: { events: 12, repeat: 1, buffer_bound: 4, event_mix: 'turn-cycle-v1' },
      baseline: [runBaselineTrial({ inputEvents: 12, bufferBound: 4 })],
      observer: [trial],
      environment,
      generatedAt: '2026-10-19T00:00:00.000Z',
    })
    expect(report.usable).toBe(false)
    expect(report.integrity.observer_failures).toBeGreaterThan(0)
    expect(report.integrity.no_event_loss).toBe(false)
    expect(report.warnings.length).toBeGreaterThan(0)
    expect(renderMarkdown(report)).toContain('Status: **NOT USABLE**')
  })

  it('marks a trial unusable when the planned record mix is not consumed', async () => {
    const trial = await runObserverTrial({ inputEvents: 60, bufferBound: 256 })
    expect(isUsableTrial(trial)).toBe(true)
    // A compactor that silently skips one planned kind (retirement or rename)
    // must not read as a usable overhead sample.
    const skippedKind = {
      ...trial,
      observedEvents: trial.observedEvents - 10,
      acceptedEvents: trial.acceptedEvents - 10,
      persistedEvents: trial.persistedEvents - 10,
      envelopeKinds: { ...trial.envelopeKinds, turn_started: 0 },
    }
    expect(isUsableTrial(skippedKind)).toBe(false)
    // A planned kind that no longer maps to a known kind still counts as an
    // observed record, so only the kind histogram can catch it.
    const substituted = {
      ...trial,
      envelopeKinds: { ...trial.envelopeKinds, turn_started: 0, unsupported: 10 },
    }
    expect(isUsableTrial(substituted)).toBe(false)
    const report = buildOverheadReport({
      config: { events: 60, repeat: 2, buffer_bound: 256, event_mix: 'turn-cycle-v1' },
      baseline: [runBaselineTrial({ inputEvents: 60, bufferBound: 256 })],
      observer: [skippedKind, substituted],
      environment,
      generatedAt: '2026-10-19T00:00:00.000Z',
    })
    expect(report.usable).toBe(false)
    expect(report.integrity.observed_matches_plan).toBe(false)
    expect(report.integrity.envelope_kind_mix_complete).toBe(false)
    expect(report.event_accounting.unconsumed_events_total).toBe(10)
    expect(report.event_accounting.unsupported_envelopes_total).toBe(10)
    expect(report.arms.observer.summary.usable_trials).toBe(0)
    const markdown = renderMarkdown(report)
    expect(markdown).toContain('Status: **NOT USABLE**')
    expect(markdown).toContain('Observed matches plan: failed')
    expect(markdown).toContain('Planned envelope mix complete: failed')
    expect(markdown).toContain('## Warnings')
  })
})

describe('observer overhead CLI arguments', () => {
  it('parses defaults and explicit values', () => {
    expect(parseArgs([])).toMatchObject({
      events: 5_000,
      repeat: 3,
      bufferBound: 4_096,
      help: false,
    })
    expect(parseArgs(['--events', '10', '--repeat', '2', '--buffer-bound', '8'])).toMatchObject({
      events: 10,
      repeat: 2,
      bufferBound: 8,
    })
    expect(parseArgs(['--json', '-', '--markdown', 'report.md'])).toMatchObject({
      json: '-',
      markdown: 'report.md',
    })
    expect(parseArgs(['--help']).help).toBe(true)
    expect(parseArgs(['-h']).help).toBe(true)
  })

  it('rejects missing or invalid values', () => {
    expect(() => parseArgs(['--events', '0'])).toThrow(/positive integer/)
    expect(() => parseArgs(['--repeat'])).toThrow(/positive integer/)
    expect(() => parseArgs(['--buffer-bound', 'nope'])).toThrow(/positive integer/)
    expect(() => parseArgs(['--json'])).toThrow(/requires a path or "-"/)
    expect(() => parseArgs(['--unknown'])).toThrow(/unknown argument/)
  })
})
