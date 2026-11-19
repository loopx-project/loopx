# S2/S12 startup diagnostic: recovering the published code after the ready budget (2026-10-30)

This entry records a repair to the managed runtime startup path. It extends
the [2026-10-04 startup diagnostic
checkpoint](2026-10-04-s2-s12-startup-diagnostic.md) and corrects the
delivery of a diagnostic the runtime had already computed.

## The gap

The 2026-10-04 checkpoint established that a fixture holding a live mutation
lock must fail with the exact `mutation_lock_timeout` code, and it explicitly
did not qualify Windows. The Windows lane fails intermittently on operations
that start the Effect runtime and wait for it to become ready, and one
retrievable run of that lane (`1e3401591`) failed with

```
assert 'runtime_startup_timeout' == 'mutation_lock_timeout'
```

`runtime_startup_timeout` is produced whenever the parent does not observe the
child's exit inside the ready loop. The baseline reads the published envelope
only when a poll *within* that loop reports an exit; after the loop it polls
once more, but only to decide whether to terminate the child, and it then
always reports the generic timeout. The historical run therefore does not
distinguish two situations: the child had not published yet when the budget
expired, or the child had already published and exited and the parent had not
observed that exit before it left the loop. The same job's pytest step ran 439
cases in 5 min 31 s, and the chunk holding the failure took two minutes
against six seconds for the chunk before it, so the runner was heavily loaded.
That load is a plausible contributor to a late publication, not a demonstrated
cause.

The startup cost itself has ample headroom. Measured on the Windows runner at
`2f544f9692` and on macOS at `d3d9ad209d`:

| Host | Ready startup | Live-lock total | Diagnostic delivered |
| --- | --- | --- | --- |
| Windows (`nt`) | 0.406 s | 5.813 s | `mutation_lock_timeout` |
| macOS (`posix`) | 0.170 s | 5.140 s | `mutation_lock_timeout` |

`STARTUP_READY_TIMEOUT_SECONDS` is 15.0 and the TypeScript
`MUTATION_LOCK_TIMEOUT_MS` is 5,000. Neither budget is the constraint, and
neither is widened here.

The exact code is lost rather than absent. The TypeScript runtime computes the
typed code and publishes it to stderr when it gives up; Python reads that
envelope only when a poll inside the ready loop shows the child has already
exited, and otherwise terminates the child at the budget and reports the
generic `runtime_startup_timeout` without reading the capture again. The
envelope is published at the moment the child exits, so a child that has not
exited has published nothing to read: at one second, three seconds and five
seconds the child is still running and stderr holds zero bytes, and the
envelope appears only once it exits, at about 5.8 s, together with exit code 2.
A child that has published and exited leaves the envelope in the capture, and
the baseline leaves it unread.

The host therefore discarded a diagnostic the runtime had already computed and
reported a generic timeout instead. The diagnostic difference is the whole
point of the typed envelope: `loopx/cli_commands/start_goal.py` gives
`runtime_startup_timeout` its own remediation text, while `mutation_lock_timeout`
falls through to the shared default, so the two codes lead the caller to
different repairs.

## The repair

`loopx/control_plane/effect_runtime.py` now gives a runtime that is still
running when the ready budget expires a bounded window in which it may publish
either its locator or its typed diagnostic. The host reads the published
envelope during that window and reports the exact code it carries.

The window is bounded by a budget the repository already owns rather than by a
new number: `STARTUP_DIAGNOSTIC_GRACE_SECONDS` is one more ready budget, so a
single startup attempt waits at most twice `STARTUP_READY_TIMEOUT_SECONDS`.
When the window closes with nothing published, the runtime is terminated and
the generic `runtime_startup_timeout` is reported, so the wait stays bounded.

A runtime that publishes its locator inside the window is now accepted instead
of terminated. Before the repair, a runtime that was merely slow was killed at
the ready budget; that outcome is unchanged in kind, only later in time.

The repair covers both situations the historical record cannot separate: a
late publication is read inside the window, and an envelope that was already
published is read without waiting for the child to exit. It therefore does not
depend on knowing which of the two the recorded failure was.

## The two acceptance points

The repair leaves two acceptance points, and this entry meets both.

**The grace window has to be bounded by something the repository already owns.**
It is: the window is one more ready budget, `STARTUP_DIAGNOSTIC_GRACE_SECONDS
= STARTUP_READY_TIMEOUT_SECONDS`, so no new budget enters the code and the
total wait per attempt is at most twice the ready budget the repository
already defined.

**The user-visible guidance changes with the code.** A caller that receives
the exact `mutation_lock_timeout` is now told to repair or reinstall LoopX,
where it was previously told that startup timed out. That difference is the
intended consequence of recovering the exact code, and it is read as such
rather than as a regression.

## What this entry does not establish

No budget change is proposed or qualified: the measurements show both budgets
are sufficient, and neither is widened. No owner-liveness change is made: the
lock in this path is held by a live owner, so the owner-liveness check
behaves correctly for it.

The repair is a diagnostic improvement, not a correctness repair. The runtime
still fails to publish its locator in the live-lock case, and the caller still
fails to start; what changes is that the caller is now told the reason. This
entry does not qualify Windows CI as passing, and the intermittent failure
set is preserved rather than reclassified.

A failed startup now blocks its caller for up to twice the ready budget
instead of once. `effect_runtime_request` retries a startup failure once for a
retry-safe call, so a caller that is failing to start can wait for two
attempts. The retry policy is unchanged here. That is the wait this startup
path adds, not an absolute ceiling for the whole call: the startup lock, the
Node probe and the outer retry each carry their own cost.

## Evidence

The repair is pinned by tests that reproduce the failure mode without a
Windows runner: the parent does not observe the runtime's published
diagnostic inside the ready loop. With the ready budget shortened and a live
mutation lock held by the current process, the pre-repair host reports the
generic timeout, and the assertion is the one the Windows lane produced:

```
assert 'runtime_startup_timeout' == 'mutation_lock_timeout'
```

After the repair the same fixture delivers the exact
`mutation_lock_timeout`, the published envelope, and the preserved live lock.
A second test pins the other situation the historical record cannot separate:
an envelope that the runtime published before the deadline but whose exit the
parent's poll missed is still read, because the parent polls once more after
the loop but the baseline never reads the capture again. Both tests report the
generic timeout on the baseline. A third test pins the bound: a runtime that
publishes nothing is terminated and still reports `runtime_startup_timeout`
after exactly one ready budget plus one grace window.

## Validation

```
uv run --extra test python -m pytest \
  tests/control_plane/test_effect_runtime_integration.py -q                    102 passed, 1 skipped
uv run --extra test python -m pytest \
  tests/control_plane/test_effect_runtime_host_permission.py \
  tests/control_plane/test_effect_runtime_node_probe.py \
  tests/control_plane/test_start_goal_compact_projection.py \
  tests/test_chat_server_cors.py -q                                            201 passed
python examples/docs-governance-smoke.py                                        docs-governance-smoke ok
python scripts/generate_rfc_status_index.py --check                             clean
scripts/check_rfc_bilingual.py --base origin/main --head HEAD                   passed
uv run --extra test loopx canary premerge --from-git-diff --git-diff-base origin/main
```

The measurement that motivated this repair is re-derivable rather than
taken on trust: the commit that produced the two figures is reachable as
`codex/measurement-archive` on the fork, and it raises a deliberate
`AssertionError` carrying the measurement, because the Windows lane runs pytest
without `-s` and would otherwise capture the output.
