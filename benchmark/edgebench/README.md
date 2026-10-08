# Native EdgeBench worker trials

This research adapter calls SForge's native `run_agent`. Task images, prompts,
submission cooldowns, judge isolation and scoring remain owned by the pinned
EdgeBench checkout. Best-only adds host capture/admission scheduling; native and
blind retain native automatic evaluation. Install SForge and Harbor in an isolated
runner environment. Run the controller on Linux with Docker and native SForge
iptables permissions; a macOS Docker socket alone does not provide that boundary.

The five worker profiles are `official`, `single`, `native-goal`,
`heartbeat-resume`, and `heartbeat-explore`. `official` uses SForge's Codex Stop
hook and outer recovery. `single` executes once with neither. `native-goal` uses
the shared native Codex Goal transport. The heartbeat profiles use the shared
LoopX scheduler and resume store. `heartbeat-explore` additionally enables the
existing Explore Graph and analysis-only Explore Harness gates with the
`adaptive-resilient` profile; this grants no child-agent execution authority.

All profiles use the same explicitly staged Codex binary and model/effort. This
permits an updated Codex version with the native scaffold; it is not a claim of
identical settings to a published leaderboard. Preserve the version receipt and
report hardware, emulation and every protocol deviation with results.

Use the offline runtime payloads documented in
[`benchmark/runtime/RUNTIME.md`](../runtime/RUNTIME.md). `CODEX_AUTH_JSON_PATH`
opts into a private local Codex login; it is copied into each disposable trial
and excluded from artifact collection. Host network isolation additionally
permits the OpenAI OAuth endpoint for refresh during long runs.

If the Linux host requires an HTTP proxy, start the restricted TLS relay with
`python -m benchmark.runtime.connect_proxy --host BRIDGE_IP --port 9090
--allow-host chatgpt.com --allow-host auth.openai.com`, with the host upstream
in `HTTPS_PROXY`. Pass `--api-proxy-url http://BRIDGE_IP:9090` to the trial.
The relay rejects other hosts and ports and never decrypts TLS. Check both an
allowed TLS handshake and a denied external destination before admission;
stop the relay process after the campaign. Do not expose an unrestricted host
proxy to isolated tasks. This transport does not grant general internet access.
`LOOPX_INSTALL_HTTPS_PROXY` may separately provide the trusted install phase's
proxy when the pinned source installer needs to build frontend assets. It is
passed only to setup subprocesses and cleared before solver execution and native
network isolation. It is never placed in the container's persistent environment.

When the product must run from main while the adapter is under development, set
`LOOPX_SRC_DIR` / `LOOPX_EXPECTED_COMMIT` to the clean product checkout and
`LOOPX_RUNNER_SRC_DIR` / `LOOPX_EXPECTED_RUNNER_COMMIT` to the clean runner checkout.
Only `benchmark/runtime` is overlaid from the runner archive; installed LoopX
code remains at the product pin. Both revisions are recorded. Without separate
runner settings, the existing single-checkout contract applies.

For the best-only default, start the dedicated online judge with the same task
and private log roots. It requires a pinned SForge revision with bounded
asynchronous judge capacity (`judge_max_concurrent` / `judge_max_pending`).
Each slot reserves one solver (4 CPUs/16 GiB) and one evaluator (4 CPUs/8 GiB):

```sh
SFORGE_TASKS_DIR=/data/tasks SFORGE_LOG_DIR=/data/private-runs \
  python -m benchmark.edgebench.online_judge --slots 2 --port 8080
```

The preflight counts existing Docker container limits and retains 2 CPUs/4 GiB
of host headroom. Unlimited containers or insufficient capacity block admission.
Run this in an operator-controlled Docker pool: the host lock coordinates this
adapter's online/offline processes, not arbitrary outside Docker launches. Keep
that reserved capacity available for the cohort. Slots are finite registrations,
not recycled when a solver finishes; start a new cohort after draining/stopping
the previous server. Native/blind use ordinary `sforge serve` instead.

An operator may explicitly pass `--allow-resource-overcommit` for a monitored
shared Docker pool. Container CPU/memory ceilings then remain enforced, but are
not treated as exclusive reservations. Startup requires available memory for
each evaluator's ceiling, up to 4 GiB per worker, and 4 GiB host headroom; unknown
container limits still block admission. The admission receipt explicitly records
zero exclusive reservation and the monitoring requirement. Monitor actual memory,
CPU pressure and grading latency throughout the cohort and stop affected trials
if sustained pressure makes operation unreliable. This mode preserves per-run
evaluation lanes but does not guarantee dedicated compute or equal latency;
record shared-pool contention when comparing experiments. For a larger explicitly
shared cohort, `--shared-startup-memory-gib 16` may replace the per-slot startup
estimate. It requires `--allow-resource-overcommit`, cannot be below one evaluator,
one worker startup allowance and host headroom (16 GiB with current limits), and
still fails on insufficient available memory or unbounded containers. This is a
startup margin, not sustained capacity qualification: retain continuous load and
grading-latency monitoring and stop affected trials on sustained pressure. Strict
admission and the shared mode without this explicit option retain their defaults.

Then run:

```sh
python -m benchmark.edgebench.run \
  --task TASK_ID --tasks-dir /data/tasks --log-dir /data/private-runs \
  --run-id UNIQUE_ATTEMPT --worker heartbeat-resume \
  --model MODEL --effort xhigh --judge-url http://HOST:8080 \
  --api-proxy-url http://PROXY_IP:9090
```

Trial timeouts use **explicit `--timeout` → [task defaults](task-defaults.json)
→ 64,800 seconds (18 hours)**. Portfolio Risk Calibration defaults to
**43,200 seconds (12 hours)**; Lean Analysis Proofs defaults to
**43,200 seconds (12 hours)**. Both task defaults apply to every worker and
feedback profile; other tasks retain the 18-hour fallback. These are total trial
budgets, including planning, not per-turn limits.

Auto-evaluation uses **explicit `--eval-interval` → task defaults → 300 seconds**.
Portfolio defaults to **300 seconds (5 minutes)**; Lean Analysis Proofs defaults
to **1,800 seconds (30 minutes)** to space out expensive compilation. Other tasks
retain the 5-minute fallback. Defaults apply equally to every worker and feedback
profile. Explicit `--eval-interval 0` disables periodic auto-evaluation in native/blind;
`best-only` requires a positive interval. The resolved
interval is passed to SForge and recorded in each attempt's runtime receipt.
These defaults affect new launches; editing the file does not change a running
sampler or create historical snapshots. Sampling cadence does not set evaluator
concurrency or replace the submission cooldown, which remains 120 seconds.
`--timeout`,
`--eval-interval`, and `--submission-cooldown` support explicitly recorded
qualification runs. The shared Harbor defaults are unchanged. Native task
internet policy is retained. Each attempt requires a new output directory.

Heartbeat and native Goal workers have no independent per-call time limit.
The shared worker uses the remaining trial budget, retaining 160 seconds for
startup/cleanup; natural completion determines continuation boundaries. A second
scheduler wake alone does not prove resume: verify another actual model
invocation with the same session identity. Native Goal owns its continuation
without an outer resume loop. Heartbeat profiles now also disable SForge outer
recovery: their LoopX scheduler owns repeated wakes, error backoff and terminal
exit. Once it exits, SForge collects the final artifacts instead of restarting
the scheduler. This changes the heartbeat transport, not LoopX's decision to
continue or end a lane; scheduler exit alone does not prove task success.
The official profile retains native outer recovery, and single/native Goal
behavior is unchanged. Record a new runner revision for new attempts; do not
rewrite earlier `outer_resume` receipts. Explicit total timeouts can support diagnostics,
but short probes are not a prerequisite for running the intended protocol.

## Feedback protocol (new-run default: best-only)

`--feedback best-only` is now the default for **new attempts across all five
workers**. This is an explicit protocol change from native, not a demonstrated
score improvement. Existing trials and pinned study manifests keep their modes.
Use `--feedback native` to retain the previous default or `--feedback blind` as
an evaluator-feedback-free control. Harbor is unchanged.

| Mode | Agent-visible evaluator feedback | Evaluation access |
| --- | --- | --- |
| native | Exact score, pass rate, counts, summary, metrics and failed names; `--details` exposes per-check messages; `--list` shows active-submission history | Agent may submit within the native cooldown/budget; automatic samples are hidden |
| blind | None; public task files, local tests and compiler feedback remain available | Host evaluates fixed automatic samples; agent has no judge route or credentials |
| best-only | Latest strict improvement notification and the corresponding submitted-source checkpoint; no score, delta, diagnostics or negative-result status | Fixed capture cadence; one evaluator and latest pending capture per run; agent cannot request extra evaluations |

Best-only supports non-game, offline tasks with `score_first`,
`valid_then_score` or `pass_rate_first` selection, including maximizing and minimizing scores. It
requires the explicit API-only proxy and a positive sampling interval. Unsupported
selection policies fail with an actionable error instead of silently changing
the task's ranking. Native grading and score selection remain unchanged. The
publisher requires the online judge and runner to share their native log root,
including each submission's original source archive. Ordinary native judges fail
the best-only admission preflight before a solver is launched.

Online scheduling keeps one evaluation in flight and one latest pending capture
per run. Every capture is archived with its original capture time and digest;
new captures supersede only the pending online candidate. A run cannot fill a
shared FIFO with stale samples, and a second run has its own reserved opportunity.
This guarantees capacity opportunity, not identical grading duration or equal
numbers of improvements. Submission retries reuse an epoch-bound capture identity;
a lost response cannot create duplicate evaluations. An unexpected judge restart
holds that attempt for reconciliation instead of silently replaying it.

The publisher accepts only that sampler's admitted submission/round identities
and verifies the original source digest. Offline/history-only results cannot
establish the baseline or change the online incumbent. The solver command's exit
pauses capture/delivery; official outer resume reuses the same publisher and lane.

After the entire online cohort ends, stop its judge and backfill all captures:

```sh
python -m benchmark.edgebench.offline_scoring \
  --trial /data/private-runs/runs/UNIQUE_ATTEMPT/TASK_ID --tasks-dir /data/tasks
```

The offline command requires terminal solver state, the original task digest and
a final capture. It acquires the same exclusive pool lock, runs native grading
sequentially, and writes a separate `offline-scoring/result.json`. Superseded and
final captures remain visible at their original times. Failed scores remain
failed; a rerun resumes saved results without silently retrying or rewriting them.
This first implementation reevaluates online captures too, favoring a complete
uniform offline record over evaluation reuse. The original native final result
covers online submissions only; use the complete offline result for post-run
qualification. Neither report by itself certifies integrity or score countability.
No offline result is routed to the worker.

The first completed valid finite score establishes a silent baseline. The task's
native selection policy is the sole improvement criterion among valid scored
snapshots. Only a strictly better native rank updates `/opt/edgebench-feedback/latest.json`; ties,
regressions, invalid/non-finite results and errors do not update it. Multiple
completed improvements observed together coalesce to the best one. Out-of-order
results compete against the best observed native rank, never against the last result.
For `pass_rate_first`, a higher pass rate can be an improvement even when its
scalar score is lower; a scalar gain with a lower native rank is silent.
When that policy returns no winner (for example, all pass rates are zero),
polling stays silent and continues normally. A later native winner can improve
the already established baseline; no scalar fallback or evaluator change is used.
Notifications describe the named **evaluated snapshot**, not the current workspace.
The source archive is the agent's own original submission, with its SHA-256; it
contains no judge output. The adapter never restores files automatically.
Before publishing an improvement, the adapter makes only the disclosed copy
readable and verifies its digest as the ordinary `agent` worker. A read or digest
failure retains the previous notification and incumbent for retry. Private host
archives retain their original permissions; evaluator and secret directories
are not made public. The hook script and system hook configuration are also
checked for ordinary-worker readability before handoff.

All five workers receive the allowlisted notification directly through managed
Codex `PostToolUse`, `SessionStart` and `UserPromptSubmit` hooks. A short synchronous
reader adds `additionalContext` before the next model request, preserving the
original tool result. It does not force a wake, interrupt active reasoning or
change Stop/continuation behavior. The official worker keeps its native Stop
hook. Delivery is serialized and deduplicated per Codex session; a fresh session
receives the latest checkpoint, while resume receives only a new one. The
reader neither queries the evaluator nor interprets arbitrary packet prose.

This transport is qualified with staged Codex 0.160.0, the actual system hook in
a disposable container and a synthetic Responses endpoint: a new result arriving
during a tool call enters the next model request; a subsequent result enters a
resumed request. Earlier Codex versions must be qualified before admission.
Inspect session input and subsequent checkpoint adoption separately: injection
proves model visibility, not use or score improvement. A missing notification
says nothing about failure: evaluation or delivery may still be pending.

A notification looks like this (digest abbreviated for illustration):

```json
{
  "schema_version": "edgebench_best_feedback_v1",
  "latest": {
    "kind": "new_best",
    "snapshot_id": "auto-7",
    "source_sha256": "<SHA-256>",
    "source_archive": "/opt/edgebench-feedback/auto-7-<SHA-256>.tar.gz",
    "message": "This evaluated snapshot strictly improved the task's native ranking among valid scored snapshots. It may differ from your current files; keep using local validation."
  }
}
```

Before the first improvement, `latest` is null. Native outer resume preserves the
same publisher and session cursor; it does not reset the incumbent. Delivery
failures retry before committing an improvement. The host-only `best-only-host`
artifacts record file publication and health; never mount or copy them into the
worker. Worker-local session hook receipts under `/logs/agent/best-feedback-delivery`
record emission, not model acknowledgement.
Treat missing archive/delivery evidence as an unqualified treatment, not as a
successful best-only trial. Public notifications do not include these errors.

Sparse feedback reduces disclosed information but still supports adaptive tuning;
it does not prove protection against evaluator overfitting or generalization.
Keep native/blind comparisons and any independent final evaluation separate.
First qualify the real network/judge boundary with the opt-in Docker smoke in
`benchmark/tests/test_edgebench_feedback_docker.py`, then compare matched tasks,
models, budgets and sampling schedules. Synthetic transport validation alone
cannot establish solver uptake or score improvement.

`--feedback native` preserves native evaluator feedback.
`--feedback blind` requires a non-game task with native internet isolation and
an explicit API-only proxy IP/port. Its host firewall omits the judge route;
native registration sets the agent submission allowance to zero, task environments
omit judge credentials, and the submission command is removed. Its prompt keeps
the task query, deliverable paths, incremental iteration, runnable files,
best-version objective and no penalty for failed attempts. Local validation
replaces official feedback instructions; submission limits and host evaluation
details are omitted. Host auto-evaluation is unchanged, and neither its existence,
schedule nor results enter the blind prompt. `agent_prompt.md` records the actual
overridden prompt, including for native Goal and heartbeat workers.

This provider-specific prompt variant lives with the Python SForge adapter; it
does not change LoopX goal authority or the official native wrapper. Earlier
blind trials used a minimal task-only wrapper and are a different protocol:
restart with a new attempt id rather than splicing their curves. Report actual
runtime separately from the time limit, and distinguish the agent's final
artifact from the evaluator-selected historical best. Prompt parity does not
establish feedback isolation or matched experimental validity by itself.
Qualify actual denied judge reads/submissions and successful host evaluation
before admitting blind trial results; a prompt or unit test alone is insufficient.
Configuration receipts prove requested startup settings; actual session model,
resume continuity, capability use, evaluator completion and integrity need
runtime/post-run qualification before any score is countable. Raw trial outputs
and credentials belong outside the public repository.

## Default replan cadence

New `heartbeat-resume` and `heartbeat-explore` trials replan after **3 settled
effective work Turns** by default. Use `--replan-after-turns N` to set a different
count (1–5), or `--replan-after-todos 3` for the previous completed-Todo cadence
as an explicit ablation. The two options are mutually exclusive and require a
heartbeat profile. This default is independent of `--task-entry`, feedback mode,
and `--turn-envelope`. Official, single and native-goal profiles are unchanged.

The shared control plane counts settled work Turns, not tool calls or idle wakes;
other replan reasons may trigger sooner. Both runtime and worker-profile receipts
record the resolved cadence. Only newly launched attempts use the new default;
keep existing runs and archived settings pinned.

## Optional short-context treatment

Add `--turn-envelope` to an otherwise identical `heartbeat-resume` or
`heartbeat-explore` command to use the product's short normal packet and local
same-invocation full detail. Other workers reject the option before creating a
trial. The default remains off; the runtime and worker-profile receipts record
it explicitly. Use a new run ID and freeze this as a separate context treatment,
keeping objective guidance, task/scorer version, model and budgets matched.
Omit the flag on the next run to disable it. No active experiment is changed by
installing this code. Inspect wake receipts and the saved decisions before
claiming adoption; score countability still requires the usual integrity review.

## Sharing terminal results

The [Portfolio ten-arm retrospective report](studies/portfolio-ten-arm-20261006/README.md)
contains recorded scores and per-arm runner settings, with explicit evaluator
cohorts and qualification limits. Use `python -m benchmark.edgebench.export_report`
to reduce an explicitly selected terminal collection or verify its settings/data
hash bindings. The adapter does not launch jobs, qualify integrity, read sessions,
or upload to a network service. Supplemental settings need publication review;
missing provenance must remain explicit. Canonical experiment-board rows continue
to use benchmark-toolkit's upload-envelope and readback contracts.
