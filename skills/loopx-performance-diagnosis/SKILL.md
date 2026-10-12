---
name: loopx-performance-diagnosis
description: Diagnose demonstrated CPU, command latency, allocation or IO regressions in an owned process with controlled measurements and language-appropriate profilers. Route benchmark effectiveness, score growth and trajectory efficiency to loopx-benchmark; profiling does not qualify a budget or grant execution authority.
---

# LoopX performance diagnosis

First identify the question. For a LoopX-managed benchmark's outcome quality,
score growth, useful progress per time/token, or agent trajectory, use
[loopx-benchmark](../loopx-benchmark/SKILL.md). A flatter score curve, repeated
reads or more tokens alone do not establish a process-performance regression.
Keep that analysis with the benchmark owner; use this skill as a focused subtask
only when evidence identifies a slow owned command or resource cost. Do not
require a profiler before analyzing effectiveness or interaction overhead.

Read the current Goal/Todo contract and repository optimization evidence rules.
State the real user operation, observed failure and owning acceptance. Preserve
the exact source, runtime/interpreter, backend/history, inputs and concurrency.
Use an owned disposable target for writes. Do not attach to a production process,
elevate privileges, weaken OS policy, or start paid/remote workloads without the
existing authorization. Raw stacks, paths, arguments and profiles stay ignored
and local-private; public summaries contain only generalized evidence.

Read `loopx/capabilities/performance_diagnosis/README.md` for tool selection and
blind spots. On installed copies use `loopx capability show performance-diagnosis`
to locate its canonical documentation. Python waits/startup: Pyinstrument; Python
threads/native on Linux: py-spy; allocations: Memray; Node/TS: V8 CPU/heap.
Go/JVM/kernel costs need their native tools. Research official sources when the
installed runtime or missing evidence makes the documented choice uncertain.
Do not choose a tool from popularity or claim one profiler covers every layer.

1. Run the uninstrumented target and record ordinary elapsed time. Use repeated
   controlled samples for comparisons; profiling time is not a baseline or p95.
2. Verify the optional tool version locally. Preserve the selected interpreter;
   install optional tools in an isolated environment when authorized. Do not
   silently install them into product dependencies or fall back on permissions.
3. Write the exact target argv to an ignored JSON array. Run
   `loopx performance-diagnosis plan --tool TOOL --command-json FILE
   --output-directory FRESH_IGNORED_DIRECTORY --format json`. Read the whole plan.
   A plan has not executed or verified tool readiness.
4. Execute `profile_argv` through the Host executor as an argv array without shell
   interpolation. Capture only the owned process; record failures and coverage
   gaps. Profile a separate Node worker instead of inferring its CPU from Python
   transport waits. Do not collect locals or automatically profile unrelated children.
5. Require actual successful target exit and nonempty artifact. Read Speedscope
   or V8 CPU using `loopx performance-diagnosis inspect --profile-json FILE
   --format json`; Memray/heap use their own reporters. Keep threads independent
   and self/inclusive time distinct. Never sum inclusive rows to obtain latency.
6. Turn the hotspot into a falsifiable hypothesis. Use a controlled intervention
   to distinguish caller/transport, shared semantics and backend cost. Reuse the
   owning contract; do not skip freshness, authority checks or decision inputs.
7. Repeat the original uninstrumented workload and semantic checks after a fix.
   Preserve passed, failed and untested results and update the existing checkpoint.
   Readback of a stack does not prove root cause, improvement or provider admission.

Do not add a new receipt/approval requirement or automatically mutate scheduling,
Goal state or telemetry. Stop invoking the workflow to disable it; remove optional
tools/captures from their owned local environment when no longer needed.
