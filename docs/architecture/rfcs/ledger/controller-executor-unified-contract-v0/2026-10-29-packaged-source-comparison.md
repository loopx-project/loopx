# Packaged source fidelity and the remaining C1 cost gap

中文镜像：[同名检查点](2026-10-29-packaged-source-comparison.zh-CN.md)。

This checkpoint supports the [single-session comparison](../../controller-executor-unified-contract-v0.md#9-validation-and-acceptance).
It qualifies source delivery and recovery on a bounded engineering workload;
it does not establish a net efficiency gain or complete C1.

## Versions and workload

The comparison pins original quota to mainline
`b201da8bab305d053866aa0d4516b97ba4a4499e` and the proposed unified contract to
`3afc2d547f6f9ca41368fd4f3f34d8f8dae9edcf` in
[#7430](https://github.com/loopx-project/loopx/pull/7430).
These are the tested versions, not a qualification of later mainline changes.
Each isolated wheel has its own verified frontend source fingerprint;
1,857 packaged product files match that arm's source, excluding generated Chat
assets, which pass their separate bundle check. Both use Python 3.14.

Each arm uses the same synthetic active Goal, full selected task and final stop,
one current-Agent outcome and five peer outcomes with four requirements each.
Both real File and SQLite authorities are initialized in disposable runtimes.
The original 24 descriptions, owners, work links and global stop conditions are
retained. No active Goal or installed host is used as a fixture.

The packaged CLI runs thin heartbeat bootstrap, one admitted App-heartbeat guard,
saved-file reads, native lifecycle hooks, warm presentation, compaction recovery,
refusal controls, writeback/spend and their replays. Hook events are synthetic;
the actual packaged hook and CLI implementations execute. A deterministic
observer reads full sources to check equivalence. That observer is not a model,
a recommendation to read every peer body, or evidence of model retention.
The actual emitted warm delta is separately read and reconstructed against its
digest-verified baseline and current files.

This is a new matched workload, not a rerun or replacement of the historical
13,000-byte experiment. The earlier **25,862 > 13,000** failure is unchanged.

## Source and recovery results

Both arms retain the selected task, all 24 requirements and global stops. Early
spending is refused. Restoring the original capture recovers the same source
without another admission; repeated writeback and spending commit one effect
and one debit, leaving the original task open.

The mainline generic codec changes the admitted source's `quota.compute` from
`1.0` to `1`; its cold, warm-recovery and missing-baseline exact-source predicates
fail on both backends. Its native cold presentation also carries `1` rather
than the admitted `1.0`. The C1 candidate preserves the original source through
these paths. This exercises the source-preservation repairs also proposed
separately in [#7661](https://github.com/loopx-project/loopx/pull/7661) and
[#7657](https://github.com/loopx-project/loopx/pull/7657), rather than creating a
caller-specific numeric policy. Those proposals and C1 adoption are distinct
from a mainline or installed rollout.

Both native warm files use `captured_file` continuity. Their actual deltas
reconstruct the current file exactly; missing baselines and damaged deltas
recover its full body. This proves saved-file reconstruction, not retained
model input. Mainline's reconstruction is exact relative to its already
normalized presentation; that does not repair the preceding source loss.

A numeric-only capture mutation distinguishes two integrity boundaries. In C1,
changing `quota.compute` from `1.0` to `1` still passes the standalone complete
contract's JavaScript-normalized hash check. Reading the changed file against
its original byte SHA-256 is refused; restoring the original bytes recovers it.
The exact-byte capture boundary is qualified for this caller. The standalone
contract's numeric commitment remains open; neither check substitutes for the
other or qualifies arbitrary Host input.

Three preliminary author runs remain failed observations: the first fixture
used a non-active Goal for refinement, the second exceeded the existing
512-character requirement limit, and the third supplied the forbidden fixed
output-directory option to `context-project`. The corrected matched cohort
uses an active synthetic Goal, bounded complete descriptions and the existing
output-root option. No product limit, assertion or acceptance criterion changes.

## Measured costs and decision

One observation per arm/backend gives the following byte counts:

| Measurement | File: quota → C1 | SQLite: quota → C1 |
| --- | --- | --- |
| Initial guard stdout (file manifest) | 5,413 → 5,440 | 5,429 → 5,456 |
| Cold saved presentation | 65,756 → 68,411 | 65,866 → 68,519 |
| Cold full-read CLI stdout | 70,568 → 73,533 | 70,680 → 73,643 |
| Actual warm delta file | 3,761 → 3,755 | 3,769 → 3,763 |
| Complete observer CLI stdout | 482,505 → 494,464 | 483,299 → 495,250 |

The six-byte warm difference includes fixture-path and surface spelling; it
does not demonstrate an encoding gain.

The observer makes 38 CLI calls in every arm, including fixture authoring,
full recovery, refusals and readback. Separate warm-file qualification adds one
CLI read per arm. In-process codec checks add nine coarse operations per arm;
C1 also performs one extra standalone-hash negative control. These are explicit
qualification costs, not a normal Turn's mandatory read policy.

The 38-call elapsed sums are 10.08 → 10.98 seconds for File and 9.41 → 9.57
seconds for SQLite. Sequential single samples, process/broker startup and the
chosen full-read observer do not establish a latency effect. The warm follow-up
timer includes CLI and codec verification and is not a CLI-latency measurement.
Memory, model tokens/cache/billing, accepted task outcomes, installed App
behavior and human-attention costs remain unmeasured. Construction and failed
author probes also remain costs; they are not erased by the completed cohort.

The useful delta is source fidelity and a more complete contract, with modest
additional cold/output bytes on this workload. There is no demonstrated net
cost reduction. Continue through existing source/context/journal owners and
independently usable repair PRs. Use actual caller failures to choose the next
hash or bridge change; do not infer a need for another full-source carrier or
parallel hash policy from an isolated assertion. Keep numeric integrity,
caller/writer cutover, historical-reader retirement and the original-quota
decision/outcome/full-cost comparison open. The optional two-session study
remains separate.
