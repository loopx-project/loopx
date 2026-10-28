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

That checkpoint selected a C1 slice to characterize necessary diagnostic compaction and
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

## Shared diagnostic owner follow-through

The follow-through at `d33c1a19d91738c66073d9e71586f14466f36d89` replaces the
Python Todo-list algorithms with `quota/diagnostic_projection.ts`. Existing
`reference_context` callers share it for should-run and the whole status/plan row
collection; scoped native composition reuses it inside its existing request.
[#7557](https://github.com/loopx-project/loopx/pull/7557), at
`5b5d9dd3b0a816aae9d5160b849cff4a3c38da13` on main
`19c213324fbb5990288bd374c1b79096f22bb152`, proposes the independently usable
legacy-reader slice as a normal PR. Its typed module is identical to the
integration module. Neither proposal is merged or installed by this work.

Only explicitly known diagnostic lanes with recognized canonical Todo records may be
bounded. An unknown item field/version, a non-record item or complex nested
content preserves that entire list. Unknown lists survive. Retained records stay
complete; portfolio prose, capability candidates, planning horizon, selected
work and existing succession IDs are not shortened or reconstructed. Original
capture/recovery remains complete. Projection metadata points to the existing
source capture instead of repeating long recovery commands in each lane.

The same frozen packaged harness and original quota commit were rerun in one
matched cohort, with the same path lengths, synthetic task and 24 clauses. The
base wheel has 1,884 source-matching product members; the candidate has 1,885.
These measurements belong to the recorded pre-rebase commit above. The later
rebase onto `19c213324` adds the unrelated merged Lark-return change; it does not
turn the frozen experiment into a measurement of that later head.

| Backend / phase | Context bytes, original → candidate | Stdout bytes | Logical requests | Elapsed ms |
| --- | ---: | ---: | ---: | ---: |
| File / cold full | 75,648 → 70,388 | 95,004 → 89,875 | 103 → 102 | 4,959.03 → 6,475.69 |
| File / warm delta | 41,252 → 44,029 | 58,401 → 61,183 | 99 → 98 | 3,465.08 → 4,538.62 |
| File / loss recovery | 74,880 → 69,618 | 90,113 → 84,903 | 6 → 6 | 5,108.18 → 5,762.51 |
| SQLite / cold full | 75,740 → 70,492 | 95,114 → 89,997 | 103 → 102 | 5,771.16 → 5,856.03 |
| SQLite / warm delta | 41,346 → 44,133 | 58,509 → 61,301 | 99 → 98 | 3,772.95 → 3,794.99 |
| SQLite / loss recovery | 74,974 → 69,722 | 90,217 → 85,017 | 6 → 6 | 4,226.47 → 5,113.92 |

Both arms still use 13/9/13 CLI calls. Expanding all five peers still adds 13,940
context bytes, 17,620 stdout bytes and 10 CLI/logical requests to each candidate
phase; measured optional-read time is 4,107–4,875 ms. The original already has
those bodies inline. The seven settlement/negative/readback calls still use 226
logical requests with identical per-backend stdout; one effect/debit and the
open task are preserved. Builds, setup, failed probes, two invalidation hooks
and optional reads remain separate recorded costs.

Cold/recovery context is about 5.3 KB smaller, but warm context is about 2.8 KB
larger and all-peer consumption remains more expensive. Every observed candidate
phase is slower in this cohort; with one observation and uncontrolled background
load this is not a causal latency estimate. An intermediate version repeated
capture commands and was larger still (File warm 45,187; SQLite 45,303 bytes);
the metadata fix does not erase that experiment or establish net benefit.

Four cold/warm File/SQLite presentations were independently compared with their
original source captures for complete selected work, boundary, horizon,
capability gate and portfolio. An intentionally mutated QA wheel that deletes
the capability gate is rejected through the packaged guard/reader path. This
checks oracle sensitivity, not model adherence. Source tampering, early spend,
same-capture loss recovery and once-only settlement retain their negative and
positive checks.

The final affected integration selection passes 144 Python and 117 typed tests;
an earlier broader selection had 533 passes and five optional skips, before the
unknown-item fallback was strengthened. The independent #7557 slice passes 264
Python/70 typed cases, including real File/SQLite status/plan/detail and canonical
Todo readback, and all 14 selected premerge checks. Status/plan now pays one coarse
typed request; explicit full Todo summaries skip it. A crowded diagnostic unit
fixture grows to 43,862 characters because complete candidates are retained; this
is an intentional information delta, not a speedup. The unchanged public v4
multi-Agent budget probe passes on original quota but fails at 27,934 > 13,000
characters on both the preceding and current integration wheels. That integration
failure remains open; no public budget was raised.

The next existing C1 work is to diagnose the warm reconstruction/metadata cost
without losing obligations, then qualify remaining consumers and writer cutover.
No positive complete-cost diff, default SQLite performance, installed App/model
retention, token/cache/billing, accepted-outcome or long-horizon benefit is claimed.
C1 and the separate C2/controller experiment remain open.
