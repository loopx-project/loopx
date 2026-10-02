# Local performance diagnosis

[中文](README.zh-CN.md)

This opt-in capability turns an owned slow operation into a profiling recipe and
reads the resulting stacks. It does not install or run tools, attach to a live
process, upload profiles, alter a Goal, or qualify a performance budget.
The Host executes the selected recipe within its existing authority.

## Placement and ownership

Capability id: `performance-diagnosis`; built-in provider: `loopx-core`.
TypeScript owns recipes and profile interpretation; Python adapts local files and
the CLI. Pyinstrument, py-spy, Memray and Node are external tool dependencies,
not newly registered extensions or automatically ready providers.
The existing `reliability-diagnostics` capability is a passive session observer
whose contract cannot express commands. `external-evidence-research` owns source
research rather than local process captures. Neither owns this caller outcome.
The workflow reuses the existing Effect transport, catalog and managed skills;
it adds no profiler daemon, execution engine, receipt gate or automatic hook.

## Choose the observation

There is no strongest profiler for every question. Choose by the missing evidence:

| Question | Start with | Important limit |
| --- | --- | --- |
| Python CLI startup, blocked calls, wall time | [Pyinstrument](https://pyinstrument.readthedocs.io/en/latest/guide.html) | Observed thread; subprocess CPU needs separate capture |
| Python threads/native stacks on Linux | [py-spy](https://github.com/benfred/py-spy) | `--idle` includes waits; native stacks need a supported platform and explicit scope |
| Python/native allocation churn or peak memory | [Memray](https://bloomberg.github.io/memray/) | Instrumentation changes cost; use its own binary reporter |
| JS/TS CPU or V8 heap | [Node profiler](https://nodejs.org/api/cli.html#--cpu-prof) | Profile the actual worker, not just its Python/Node launcher |
| Python vs native/system work and copying | [Scalene](https://github.com/plasma-umass/scalene) | Optional second opinion; AI suggestions are not required or automatically enabled |
| Kernel, disk, locks or off-CPU waits | [Linux perf/BCC](https://github.com/iovisor/bcc), [samply](https://github.com/mstange/samply), platform Instruments | OS privileges and symbols matter; never weaken system policy automatically |
| Go / JVM | [pprof](https://go.dev/blog/pprof) / [async-profiler](https://github.com/async-profiler/async-profiler) | Follow the runtime's native capture and analysis interface |

The CLI provides five tested recipe shapes: `pyinstrument`, `py-spy`, `memray`,
`node-cpu`, `node-heap`. Other tools above are escalation guidance, not implemented
adapters or a claim that they were validated on every platform.

## Run and read back

Keep captures in an ignored, fresh directory. Verify target ownership, source,
runtime/tool versions, input size and concurrency before executing it. Use a
disposable fixture for a write command. A recorded stack can include paths and
command arguments; do not publish the raw files or treat them as telemetry.

For a Python module, first install the optional tool into an isolated environment
that can import the target (or expose that tool on the chosen interpreter).
Do not replace the target interpreter or silently modify production dependencies.

```bash
mkdir -p .local/diagnosis/run-1
printf '%s\n' '["python", "-m", "loopx.cli", "--format", "json", "version"]' > .local/diagnosis/command.json
loopx performance-diagnosis plan --tool pyinstrument \
  --command-json .local/diagnosis/command.json \
  --output-directory .local/diagnosis/run-1 --format json
```

The returned `baseline_argv` is unchanged. Execute `profile_argv` as an argv
array through the Host executor, with no shell interpolation. A plan reports
`execution_performed=false` and `tool_readiness_verified=false`; tool availability,
exit status, file existence and actual readback still need evidence.
Pyinstrument/Memray recipes accept a script or `-m module` target without Python
interpreter flags. py-spy writes its own progress beside target stdout: a Host
must capture structured target output and the target's exit status separately;
do not feed mixed profiler output back into a JSON protocol parser.

```bash
loopx performance-diagnosis inspect \
  --profile-json .local/diagnosis/run-1/profile.speedscope.json --top 15 --format json
```

For Node use `--tool node-cpu` and a command array beginning with the exact Node
executable and actual script/module arguments; inspect `profile.cpuprofile`.
For Memray and V8 heap captures use their native allocation reporters. The
summarizer rejects unsupported, empty or malformed formats rather than emitting
an empty successful diagnosis. Speedscope sampled/evented and V8 CPU time units
are normalized to milliseconds. Threads/profiles stay separate, recursion is
counted once per stack observation, and self and inclusive hotspots are distinct.
Do not sum inclusive rows or thread weights to infer elapsed time.
Input files are limited to 16 MiB; shorten captures beyond that limit. Repeated
profile names and hotspot labels share a 1 Mi-character display-text budget;
oversized summaries are rejected explicitly, never silently truncated. Larger
recordings reuse Effect's private local snapshot transport without raising the
ordinary 2 MiB message limit or returning the raw profile in the summary.

## Complete the diagnosis

1. Measure the original command without profiling; pin source, backend/history,
   cold/warm conditions, input and host load. A single sample is not p95 evidence.
2. Capture the process that owns the suspected cost. Record uncovered threads,
   child processes, native code and kernel waits.
3. Read back the capture. Treat stacks as a hypothesis; test a controlled change
   that separates caller, transport/shared semantics and provider costs.
4. Repeat the same uninstrumented workload and semantic readback. Retain failed,
   passed and untested rows. Never replace an admission failure with profile time.

[The Python profiler documentation](https://docs.python.org/3/library/profile.html)
explicitly distinguishes profiling from benchmarking. Follow the existing
[optimization evidence guide](../../../docs/development/testing-and-quality.md#roadmap-aligned-optimization)
and the owning RFC; this capability introduces no extra approval or receipt gate.

Disable by not invoking the command/workflow; there is no background collection.
Remove optional tools from their isolated environment and local captures after
retaining the required private evidence. This grants no production attach,
elevation, Goal mutation, upload or merge permission.

## Frontend inspection

Open **Settings → Capabilities → Device defaults → Performance diagnosis**.
Choose a local Speedscope or V8 CPU JSON capture (up to 16 MiB). The packaged
frontend parses it in a cancellable worker using the same TypeScript rules as
the CLI, displays each profile separately, and lets you rank self or inclusive
hotspots. Invalid captures replace the earlier result with an error.

Files and results remain in browser memory: no upload, saved preference, process
attachment or automatic collection. **Clear / cancel** drops the result and
terminates its worker; leaving settings does the same. Capture still requires
an explicitly owned Host target; the browser does not gain process privileges.
