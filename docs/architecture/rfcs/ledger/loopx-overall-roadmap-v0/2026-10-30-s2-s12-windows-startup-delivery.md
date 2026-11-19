# S2/S12 startup diagnostic: Windows delivery window (2026-10-30)

Measured on `main` at `d3d9ad209d`. This entry records what the
[2026-10-04 startup diagnostic
checkpoint](2026-10-04-s2-s12-startup-diagnostic.md) did not establish on
Windows, and corrects the premise that produced an earlier wrong conclusion
about the startup budgets. It changes no runtime behavior.

## What the earlier checkpoint established, and what it left open

That checkpoint records that when the managed Effect runtime cannot publish its
startup locator, the failure travels through the typed startup envelope and
reuses the shared filesystem/lock codes, and that a real fixture holding a
live mutation lock "fails exactly once with the exact code". Its evidence
was a real fixture; it did not qualify Windows, and it explicitly did not
claim that Windows CI passes.

## Measured

The Windows lane fails intermittently on operations that start the Effect
runtime and wait for it to become ready. The failing set differs per run:
`test_effect_runtime_integration`'s `live_lock` case,
`test_shadow_observation_retirement`, `test_chat_manager_inspection`.

Across six Windows lane runs whose job logs are still retrievable, the
failing file set was `test_self_update_runtime_activation` alone (three runs),
that file together with `test_shadow_observation_retirement` (one run), that
pair together with `test_effect_runtime_integration` (one run), and
`test_chat_manager_inspection` alone (one run).

The `live_lock` case failed on Windows at `1e3401591` with
`assert 'runtime_startup_timeout' == 'mutation_lock_timeout'`, and passed in
the later Windows runs of this entry's own measurement. It carries no platform
skip, so it does run on the Windows lane.

The startup cost was measured on the Windows runner at `2f544f9692` and on
macOS at `d3d9ad209d`:

| Host | Ready startup | Live-lock total | Diagnostic delivered |
| --- | --- | --- | --- |
| Windows (`nt`) | 0.406 s | 5.813 s | `mutation_lock_timeout` |
| macOS (`posix`) | 0.170 s | 5.140 s | `mutation_lock_timeout` |

The two revisions differ in `effect_runtime.py`, but only in the runtime-source
metadata read, `_control_plane_root` and `_request_with_info`. The startup
path itself — `_start_runtime`, `STARTUP_READY_TIMEOUT_SECONDS`,
`_read_startup_stderr` and the `process.poll()` check — is unchanged between
them, so both hosts are measured on the same startup code.

The live-lock total is the ready startup plus the TypeScript mutation-lock
wait, and both budgets have ample headroom: `STARTUP_READY_TIMEOUT_SECONDS`
is 15.0 and `MUTATION_LOCK_TIMEOUT_MS` is 5,000.

**This corrects an earlier conclusion.** The hypothesis that "node cold start
plus the 5 s lock wait exceeds the 15 s ready budget on Windows" is false. The
cold start is 0.406 s, and the live-lock case returned the exact
`mutation_lock_timeout` code on that same run. Neither budget should be
widened to explain this failure.

## Where the exact diagnostic is lost

TypeScript computes the typed code and publishes it:
`effect_runtime_server.ts` `failStartup` writes the startup envelope to
stderr with `writeSync` and then calls `process.exit`. Python reads that
envelope only when a poll *inside* the ready loop shows the child has already
exited (`effect_runtime.py`); after the loop it polls once more, but that
poll only decides whether to terminate the child, and the function then
reports the generic `runtime_startup_timeout` without reading the capture
again.

So the exact code is produced but not delivered whenever the parent does not
observe the child's exit inside the ready loop.

The lock in this path is **not** a stale lock. The fixture writes the
current process's own PID as the lock owner, so the owner is alive and
`reclaimStaleMutationLock` correctly refuses to reclaim it; waiting out the
5 s mutation-lock budget is the intended behaviour, on every platform. The
owner-liveness check in `effect_runtime_io.ts` records only a PID, so a
reused PID or an `EPERM` probe result would also read as alive, but that is
not what this fixture exercises and this entry does not claim it as the
cause here.

The measured live-lock total is 5.813 s on Windows and 5.140 s on macOS
against a 15 s ready budget, and the exact code was delivered on both. So
the budget is not the constraint in the measured runs.

The intermittent Windows failure at `1e3401591` is a different situation. Its
job log is retrievable and shows
`assert 'runtime_startup_timeout' == 'mutation_lock_timeout'`. That code is
produced whenever the parent does not observe the child's exit inside the
ready loop, and the historical run does not distinguish two situations: the
child had not published yet when the budget expired, or the child had
already published and exited and the parent had not observed that exit
before it left the loop. The same job's pytest step ran 439 cases in 5 min
31 s, and the chunk holding the failure took two minutes against six seconds
for the chunk before it, so the runner was heavily loaded. That load is a
plausible contributor to a late publication, not a demonstrated cause, and
this entry does not settle which situation occurred.

Reading the capture at the moment the budget expires recovers the envelope
in the second situation only. Tracing one child process shows the ordering
directly: at one second, three seconds and five seconds the child is still
running and stderr holds zero bytes, and the envelope appears only once the
child exits, at about 5.8 s, together with exit code 2. The TypeScript side
reaches `failStartup` only after the mutation-lock budget is spent, so a child
that has not exited has published nothing; a child that has exited leaves the
envelope in the capture, and the baseline leaves it unread.

It can be recovered by letting the child finish first, or by reading the
capture again once the child has exited. Waiting for the exit instead of
terminating at the budget yields the exact code in every measured case, and
the extra wait is bounded rather than open-ended: the child's exit lands at
5.13 s to 5.17 s regardless of whether the ready budget was 0.5 s, 1 s or 2 s,
because what the child waits for is the TypeScript mutation-lock budget. A
repair would therefore give the child a bounded grace window after the ready
budget, read the envelope if it appears, and report the generic timeout only
when the child does not finish inside that window. Such a repair covers both
situations above, so it does not depend on knowing which one the recorded
failure was. That repair is not made here.

## What this entry does not establish

No budget change is proposed or qualified; the measurements show both budgets
are sufficient. No owner-liveness change is proposed here: the lock in this
path is held by a live owner, so the owner-liveness check behaves correctly
for it, and changing how an owner identity is recorded would be a runtime
change with its own acceptance that this measurement does not justify.

The intermittent Windows failure is now attributed to a parent that did not
observe the child's exit inside the ready loop; the historical record does
not distinguish a child that had not published yet from a child that had
published and exited unobserved, and the heavily loaded runner is a
plausible contributor rather than a demonstrated cause. A bounded grace
window after that budget is the repair this entry identifies, and it covers
both situations, so it does not depend on knowing which one occurred. The repair itself is
not made here: giving the child a grace window changes how long a failed
startup blocks its caller, and that needs its own acceptance and a caller
that tolerates the longer wait.

Two acceptance points follow from it. The caller now waits longer, so the
grace window has to be bounded by something the repository already owns
rather than by a new constant. And the user-visible guidance changes with
the code: `loopx/cli_commands/start_goal.py` gives `runtime_startup_timeout`
its own remediation text, while `mutation_lock_timeout` falls through to the
shared default, so a caller that receives the exact code would be told to
repair or reinstall LoopX where it was previously told that startup timed
out. That difference is the intended consequence of recovering the exact
code, and it has to be read as such rather than as a regression.

The delivered typed code is a diagnostic improvement, not a correctness
repair: the runtime still fails to publish its locator, and the caller still
fails to start. Windows CI is not qualified as passing by this measurement,
and the intermittent failure set is preserved rather than reclassified.

The measurement itself was a temporary diagnostic, removed after the numbers
were read; it is not a qualification artifact in this repository. The commit
that produced the numbers is reachable as `codex/measurement-archive` on the
fork, so the two figures can be re-derived rather than taken on trust: it
raises a deliberate `AssertionError` carrying the measurement, because the
Windows lane runs pytest without `-s` and would otherwise capture the output.
