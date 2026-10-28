# Packaged single-session heartbeat comparison

This is a measurement checkpoint for the [controller/executor RFC](../../controller-executor-unified-contract-v0.md#9-validation),
not acceptance of C1 or a default promotion. The [Chinese mirror](2026-10-28-packaged-heartbeat.zh-CN.md)
records the same scope and results.

## Source and workload

Compare original quota at main `843be494266774b63b3de61d0adf5dd7f4f877a2`
with the integration candidate at runtime commit
`1199cbe0b8e92ddf845e345483041486fe808024` in [#7430](https://github.com/loopx-project/loopx/pull/7430).
Each wheel was built with its own source-dependent frontend fingerprint;
1,884 packaged product members matched their respective source checkout.
No global runtime was installed or active Goal state used as a test fixture.

Both arms use the same synthetic workload and real, isolated File and SQLite
authorities: one active Goal, a complete selected task, one current-Agent outcome
and five independent peer outcomes, with four requirements each. Global intent,
authority and stop conditions remain available. The candidate's existing native
context opt-in expands the current outcome and indexes the peers. Every peer
body remains readable from the original capture; all 24 requirement descriptions,
owners, linked work and unverified states were checked after expansion.

The packaged CLI path runs thin heartbeat bootstrap, guarded quota/native context,
every required context page, mandatory Goal-source reads, and the real context-hook
command. Synthetic native lifecycle events provide warm continuity and then
PostCompact/SessionStart invalidation. Loss recovery reads the **same original
capture**, without replacing admission. Tampered sources and early spending are
rejected; refresh and spend replays preserve one effect and one debit. The
unfinished selected task remains open. Warm reconstruction and missing-baseline
full fallback were also checked.

These are packaged engineering observations with simulated host continuity.
They do not establish installed Codex App adoption, model retention or decisions,
SDK coverage, accepted task outcomes, or a controller/executor experiment.

## Observations

One matched cohort was run, one observation per arm/backend/phase. Background
host load was uncontrolled. Elapsed time sums actual CLI calls, including startup,
required Goal reads and lifecycle observations. Counts are logical Python-to-runtime
requests; physical framing, retries and provider-internal IO are not inferred.
Artifact size is delivered UTF-8 context content, not tokens or the full stdout.

| Backend / phase | Context bytes, original → candidate | Logical requests | Elapsed ms |
| --- | ---: | ---: | ---: |
| File / cold full | 75,387 → 76,162 | 103 → 102 | 5,052.49 → 4,867.53 |
| File / warm delta | 40,991 → 43,558 | 99 → 98 | 3,240.32 → 3,399.05 |
| File / loss recovery | 74,619 → 75,390 | 6 → 6 | 4,045.91 → 4,213.20 |
| SQLite / cold full | 75,483 → 76,270 | 103 → 102 | 4,566.19 → 4,428.81 |
| SQLite / warm delta | 41,085 → 43,666 | 99 → 98 | 3,236.50 → 3,416.50 |
| SQLite / loss recovery | 74,713 → 75,498 | 6 → 6 | 4,149.99 → 3,957.72 |

Both arms use 13/9/13 CLI calls for cold/warm/recovery. Invalidation itself is
separate: two hook calls, 683–738 ms across these observations. Expanding all five
peers in the candidate adds 13,940 content bytes, five reader pages and five
observation hooks: **10 CLI calls / 10 logical requests**, 17,620 stdout bytes,
and 3,416–3,713 ms per phase. The original arm already carries these bodies inline.

Total phase stdout also grows: File cold 94,662 → 96,082 bytes, warm
58,077 → 60,649, recovery 89,807 → 91,144; SQLite 94,776 → 96,208,
58,185 → 60,771 and 89,911 → 91,262. The seven settlement/negative/readback calls
per backend use 226 logical requests in each arm, with identical stdout totals
within each backend. Setup, builds, failed authoring probes and optional reads
are separate costs, not removed from the experiment history.

Coalescing acceptance/title/Explore projection removes one logical request and
reduces bridge JSON. For example, File cold request/response content falls from
965,261/585,535 to 902,963/494,206 bytes. That local reduction does **not** offset
the larger delivered artifact or optional expansion. Warm elapsed time rises in
both backends; recovery observations are mixed. This sample supports no causal
speedup, default SQLite performance qualification or positive complete-cost diff.
Tokens, cache use, billing, verification outcomes, human attention and long-horizon
net benefit remain unqualified. Earlier negative and mixed measurements stand.

## Owning gap and continuation

Compared with original quota, scoping removes about 13 KB of peer bodies, but the
candidate retains about 6.6 KB of repeated Todo diagnostic lanes and adds about
3.9 KB of scope metadata. Candidate lists and other diagnostic repetitions consume
the remaining savings. This is a composition/compaction gap, not evidence that
peer scope itself lowers total cost.

The next C1 slice must characterize necessary quota diagnostic compaction and
move it into the existing shared typed composer, retiring the replaced Python
caller algorithm. Preserve complete selected work, portfolio/horizon,
claim/lease facts, global obligations, unknown fields, exact references and
historical recovery. The old Python compactor drops unknown list fields and
shortens some prose; copying it unchanged would not meet that contract. Reuse
the same packaged comparison for default reads and all-peer expansion before
claiming improvement. No new packet format or per-field RPC is justified.

The integration candidate adopts the merged route owner (#7520) and reference
owner (#7457). Acceptance, Task-title and Explore legacy references now share
one existing `reference_context` operation; the redundant acceptance operation
and duplicate controller-input dispatch branch are removed. Complete composition
reuses those same semantic functions. This is a bounded convergence change,
not full writer cutover, historical-reader retirement or C1 completion.

The final affected suite has 469 passes and five optional native/SDK skips;
116 typed tests and control-plane typechecking pass. Earlier build fingerprint,
missing-dependency, harness-carrier and outdated call-count failures are retained
as authoring/validation costs, not silently converted into product passes.
No model, paid evaluation, managed benchmark, live install, release or merge was
performed for this comparison.
