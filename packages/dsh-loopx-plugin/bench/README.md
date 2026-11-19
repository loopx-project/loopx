# Observer overhead bench

Measure the DSH shadow observer's component cost on a deterministic synthetic
event stream. This is a development tool for the P0 "measured overhead" gap in
[#5211](https://github.com/loopx-project/loopx/issues/5211); it is not a live
DSH host, model, or C1 qualification.

## What it measures

- **Two arms**: a baseline driver that walks the same planned stream without an
  observer (no serialization or appends), and the real `ShadowObserver`.
- **CPU**: `process.cpuUsage()` user + system delta.
- **RSS**: process RSS before/after each trial.
- **Ledger append**: p50/p95 latency through the shipped `appendLedgerLines`
  writer, envelope bytes per persisted envelope, and stats bytes per flush.
- **Event accounting**: observed, accepted, persisted, dropped (total and max),
  rejected, peak buffer, and flush attempts. Counts must satisfy
  `observed = accepted + rejected + dropped`.
- **Clock**: the harness-time vs wall-clock envelope mix and the maximum
  declared uncertainty.
- **Integrity**: no event loss, persisted envelopes equal accepted envelopes,
  every planned record observed with the planned envelope-kind mix, no outbound
  endpoints, no worker-context influence, and no scheduler-input influence.

A trial that drops or rejects events, fails an append, persists fewer envelopes
than it accepted, or does not consume the planned record and envelope-kind mix
is excluded from the arm summaries. The report is then marked `NOT USABLE`,
prints the per-trial loss and peaks, and lists warnings. Do not use such a run
as an overhead sample: rerun with a larger `--buffer-bound` or fewer `--events`
(or fix the bench mix or observer when planned kinds stop being consumed).

The report is public-safe: it contains aggregate numbers, the Node/platform
version, and limits. It does not contain goal ids, session ids, ledger paths, or
raw event content.

## Run

From `packages/dsh-loopx-plugin`:

```bash
node --experimental-strip-types bench/observer-overhead.ts \
  --events 10000 \
  --json ../../.local/observer-overhead.json \
  --markdown ../../.local/observer-overhead.md
```

Defaults are 5,000 events, 3 repeats, and a buffer bound of 4,096. The bench
uses a larger bound than the observer's product default (256) because the
synthetic producer emits events far faster than a real session; pass
`--buffer-bound 256` to mirror the product default and exercise backpressure
drops. `--events` is normalized down to a whole six-event turn cycle: 5,000
becomes 4,998 and 10,000 becomes 9,996, and the report prints both counts. The
tool runs a short warmup, then alternates baseline and observer trials. Use
`--json -` or `--markdown -` to print a report to stdout.

The observer requests a flush at every turn end, but an append still in flight
coalesces later requests, so the number of append batches (and the peak buffer)
depends on I/O latency. The report prints the actual flush attempts and peak
buffer; treat the per-event numbers as a signal, not a workload forecast.

Example output from one Windows/Node 26 run of `--events 10000` (numbers are
machine-specific and are not a qualification):

```text
- Status: **usable**
- Events per trial: 9,996 (requested 10,000; normalized to whole 6-event turn cycles)
- Buffer bound: 4,096

| Metric | Baseline | Observer | Delta |
| --- | ---: | ---: | ---: |
| CPU total, median (ms) | 0.00 | 906.00 | 906.00 |
| CPU, median (ms / 1k events) | 0.00 | 90.64 | 90.64 |
| RSS delta, median | 448.0 KiB | 13.1 MiB | 12.7 MiB |
| Ledger append p50 (ms) | n/a | 1.49 | n/a |
| Ledger append p95 (ms) | n/a | 2.09 | n/a |
| Ledger bytes / envelope | n/a | 490.38 | n/a |
| Ledger stats bytes / flush | n/a | 1326.25 | n/a |
| Persisted envelopes, median | n/a | 9,998 | n/a |
| Clock source, median (harness / wall-clock) | n/a | 9,996 / 2 | n/a |
| Clock uncertainty max, median (ms) | n/a | 50.00 | n/a |

| Observed | Accepted | Persisted | Dropped (total) | Dropped (max) | Rejected | Flush attempts | Peak buffer (max) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 9,998 | 9,998 | 9,998 | 0 | 0 | 0 | 218 | 84 |

- Planned records not observed: 0
- Unsupported envelopes: 0
- No event loss: passed
- Persisted equals accepted: passed
- All trials usable: passed
- Observed matches plan: passed
- Planned envelope mix complete: passed
```

The clock row shows the split: workload events carry a harness timestamp, and
the session-created/disposed records use the observer wall clock with the
declared 50 ms uncertainty. That mix is the measured timing uncertainty; the
maximum is the observer's declared bound for those two lifecycle records and does
not vary with the stream.

## What it deliberately does not measure

- Cordis hook dispatch or DSH scheduling;
- model, network, or tool latency;
- a real session's worker outcome;
- C1 eligibility or P0 exit.

Those require a separately authorized live run and maintainer acceptance. See
the [capability README](../../../loopx/capabilities/reliability_diagnostics/README.md)
and the [RFC](../../../docs/architecture/rfcs/long-running-agent-reliability-diagnostics-governed-delivery-v0.md).

## Test

```bash
pnpm test -- observer-overhead
```

The test covers the event-plan shape, percentile boundaries, the shipped-writer
append path and byte split, loss, append-failure, and planned-record-mix
marking, CLI parsing, and public-safe report rendering.
