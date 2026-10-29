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

## Relocated-context integration

Runtime `167b31e5d078193b9fd73ad6609804e268390972`, rebased onto main
`f205c02a37d5a668c956733a721af911dcac7fab`, adopts the existing
[#7528](https://github.com/loopx-project/loopx/pull/7528) codec change at
`759c16a9cf430bb7442b47dd3a5c3336ffb93666`. The integration keeps main's scoped
preference binding, native checkpoint fixtures and intelligent-mediation design.
It does not create another encoder. One exact baseline subtree plus literal
current overrides replaces repeated paths to unchanged evidence. Unknown and
current controls remain inline; original capture, identity and recovery stay
unchanged. A scoped-wrapper regression exercises the same implementation.

A 24-row synthetic relocation compares that runtime with its immutable parent
`87e216f24f966671d35e035df7ba285248b48bad`. Complete ordinary delta bytes fall
13,199 → 6,090; the C1 scoped-wrapper delta falls 13,809 → 6,230. Cold/current
full encodings remain 20,060/20,188 bytes and 20,167/20,295 bytes respectively.
Both reconstruct exactly; the actual old decoder recovers the same full current
capture for the new overlay. The original missing-overlay case fails before
adoption. These are codec observations, excluding model retention, tokens,
framing, billing and task outcomes.

The unchanged packaged harness is rerun in a new matched cohort against original
quota `843be494266774b63b3de61d0adf5dd7f4f877a2`. Both arms use the same fixture
and equal path lengths; their 1,884/1,885 product members match their sources.
These results belong to this runtime and cohort; the earlier tables remain
historical observations of their own sources.

| Backend / phase | Context bytes, original → candidate | Stdout bytes | Logical requests | Elapsed ms |
| --- | ---: | ---: | ---: | ---: |
| File / cold full | 75,211 → 69,934 | 94,432 → 89,286 | 103 → 102 | 5,961.28 → 5,183.22 |
| File / warm delta | 40,817 → 43,575 | 57,861 → 60,624 | 99 → 98 | 3,981.29 → 3,592.23 |
| File / loss recovery | 74,445 → 69,164 | 89,603 → 84,374 | 6 → 6 | 5,052.05 → 5,156.02 |
| SQLite / cold full | 75,311 → 70,044 | 94,550 → 89,414 | 103 → 102 | 5,205.45 → 6,393.69 |
| SQLite / warm delta | 40,913 → 43,683 | 57,971 → 60,746 | 99 → 98 | 3,482.77 → 4,216.01 |
| SQLite / loss recovery | 74,541 → 69,272 | 89,709 → 84,492 | 6 → 6 | 4,649.12 → 5,590.37 |

CLI counts remain 13/9/13. All-five-peer expansion still adds 13,940 context
bytes, 17,620 stdout bytes and ten CLI/logical requests per candidate phase;
observed optional-read time ranges from 4,046 to 9,431 ms. Seven negative,
settlement and readback calls retain 226 logical requests and equal per-backend
stdout in both arms. Tamper/early-spend rejection, all 24 clauses, same-capture
recovery, one effect/debit and the open task pass. Four source comparisons keep
selected work, boundary, horizon, capability gate and portfolio complete; a new
mutated QA wheel dropping the capability gate is rejected by the real guard and
reader. No other owned build or test overlaps the timed cohort; host background
load remains uncontrolled. One observation does not qualify latency or p95.

The final typed selection passes 147 cases and typecheck. The affected Python
run records 299 passes, 51 optional native/SDK skips and two recovery-text size
failures in a long temporary namespace. A controlled identical-path comparison
on immutable main and this runtime reproduces both failures at 2,015 characters;
neither hook embeds the requirement body, and both retain the original recovery
command. The normal-path lifecycle suite separately passes all 145 cases. No limit,
assertion or fixture body was relaxed, and the original failing run is retained.
The new wheel required rebuilding main's stale frontend bundle before successful
packaging. The unchanged v4 probe still fails at 27,934 > 13,000 characters.

Relocation redundancy is reduced, while ordinary warm input still grows about
2.8 KB and all-peer reading remains more expensive. The latter is a different
remaining C1 composition/metadata gap. Default SQLite performance, installed App
and model retention, token/cache/billing, accepted outcomes and full long-horizon
benefit remain unqualified. Continue the existing C1 on that gap and remaining
consumers; this adoption does not close C1, C2 or the controller experiment.

Subsequent integration is rebased onto main
`0cfbf11dcea4ba7bc8fb13f06f1c915b63ce8fad`, which contains #7528; its duplicate
commit drops out. Both Goal-source commits from [#7547](https://github.com/loopx-project/loopx/pull/7547)
retain their authorship and all 12 contributed product/test blobs match the
accepted integration head. Main's capture owner still stages before admission
and publishes afterward. The original complete contract is installed only after
publication. Removing an unused capture helper restores the process-level fence
around the actual Turn executor; the existing held-lane test fails before this
repair and passes afterward.

Runtime `6f30cc454f2dea7e77f0821a14a1deaf268efe2e` has a source-matching
1,885-member wheel. The post-rebase typed selection passes 148 cases and
typecheck. The integration Python selection initially records 185 passes, two
explicit live-host skips and 12 failures: eight new staged-readback assertions
compare adapter metadata with the actual model projection, and four observers
still name the pre-#7547 metadata-only operation. Retaining their complete
comparisons against the real Host input and observing the stricter dispatch
operation makes all 54 Host-capture cases pass; no product gate is relaxed.
The earlier run and its other passed cases remain recorded separately.

The packaged File/SQLite bootstrap, complete Goal delivery, all clause/detail
reads, same-capture recovery, rejection and once-only settlement pass again on
this wheel. This functional run overlaps other validation and is not a new
matched timing cohort. The older cost tables remain attached to runtime
`167b31e5d`, not relabeled as this later source. Current-version complete-cost
and outcome qualification remain open.

The committed premerge then exposes a maintainability failure: the executor
contains 94 statements against the existing 90-statement limit. Runtime
`82f93ab76a403c2633efedb2f8bf02f85be8e3c6` moves published-input installation
into the existing staged-capture owner, preserving recovery bindings and
publication ordering. Its source-matching 1,885-member wheel, all 54 Host-capture
cases and the unchanged maintainability ratchet pass. The original failure is
retained; no exception or threshold is added. This owner consolidation does not
turn the historical warm-input or v4 budget failures into passes.


The next integration rebases the retained C1 history onto main
`6f8f476388645fcea2269329d32974b559d490e6` and adopts the repaired diagnostic
slice [#7557](https://github.com/loopx-project/loopx/pull/7557) at
`eb4e9b970aeb1ffb83e46705d6386cc3a4679ead`. That slice originally transported
the whole Python input through JavaScript JSON: `1.0`, negative zero and integers
above the safe range could change outside the display fields. Nine independent
source-number cases fail before repair. The shared typed owner now returns only
display edits and immutable source references; Python applies them to its original
values without owning compaction policy. The standalone slice passes 305 Python
cases, 99 typed cases, typecheck, Ruff and its standard premerge. These are source
and real File/SQLite CLI checks, not installation or independent acceptance.

C1 runtime `7f35454b392e09ae574c5065c473ee27b5eee853` uses the byte-identical
shared diagnostic module. Its acceptance-sharing stage formerly cloned every
source object, defeating references for relocated numeric facts. It now copies
only changed registered carrier paths. The first integration selection retains
253 passes and five failures: three expose that clone and two old boundary
fixtures fail the complete-Goal dispatch requirement, also on the previous C1
head. The boundary fixtures now keep work in its canonical provider and deliver
complete non-Todo Goal material; they check both the current original-contract
Host input and historical scope aliases, including rejection, without relaxing
dispatch. The final affected selection passes all 258 Python cases; the 110-case
typed selection, typecheck and Ruff pass. The older wheel and matched timing
cohorts remain attached to their actual versions. This source follow-through
does not qualify a new packaged cohort, cure the warm-input or v4 budget failures,
or close remaining consumers, reader retirement or actual model/outcome costs.


The additional complete-Host selection retains 200 passes and two failures
against the later main integration: its operation-host oracle still expected
whitespace folding, removed by main's [#7549](https://github.com/loopx-project/loopx/pull/7549).
The actual `chat_agent.py` blob equals the verified main base. Checking original
prompt bytes on both transports, while retaining schema, retry and tamper
assertions, makes all 54 Host-capture cases pass. This changes the test oracle,
not the production transport. The initial failure remains recorded.

GitHub subsequently reports #7557 merged as
`4b2b97b7b726d902a20969e3911fd15d15b4bb2e`; all seven contributed files match
its repaired head. Merge occurrence does not supply a missing new-head
independent review or protected-action receipt. The C1 qualification above
remains bound to its frozen main base; later main changes are not implicitly
qualified. C1 stays an unfinished author integration with its existing cost,
consumer and retirement gaps.

## Complete Goal reuse and independently usable native slice

C1 is subsequently rebased onto main
`f5e9be3671c1b5c80eba5c62fe4d617bfefd66d6`. Runtime
`a0cc4c14b` reuses the exact unchanged scoped-reading instruction and objective,
while keeping every other wrapper field current. It also reuses complete Goal
text only after the existing source-integrity predicate succeeds. That predicate
moves unchanged into `goal_context_source.ts`, shared by fulfillment and the
codec. Changed text, control fields, scope, indexes, ownership, revisions and
unknown fields stay literal; malformed witnesses and missing baselines cannot
hide required reads. This removes repeated interpretation rather than adding
another context or decision owner.

Source `ebdce8a49b4faa584571cfb688aa2c2a238a5860` retains this behavior and
native complete-Goal regression coverage. Its rebuilt wheel has 1,889 product
members matching source, with no packaged-only or mismatched member. The same
unchanged private harness compares it with the original quota baseline
`843be494266774b63b3de61d0adf5dd7f4f877a2`, using equal-length namespaces and
the same six outcomes, 24 clauses, work, stops and backends. Both arms include
mandatory supplementary Goal-file reads; recovery also includes the two
invalidation/startup hook outputs and reads the original capture without a new
admission. Optional peer expansion is separate.

| Backend / phase | Original quota stdout bytes | C1 stdout bytes | Calls, quota → C1 | RPCs, quota → C1 |
| --- | ---: | ---: | ---: | ---: |
| File / cold | 94,892 | 90,918 | 13 → 14 | 103 → 103 |
| File / warm | 58,289 | 52,474 | 9 → 8 | 99 → 98 |
| File / loss recovery | 92,068 | 87,418 | 15 → 14 | 8 → 8 |
| SQLite / cold | 95,002 | 91,048 | 13 → 14 | 103 → 103 |
| SQLite / warm | 58,401 | 52,598 | 9 → 8 | 99 → 98 |
| SQLite / loss recovery | 92,178 | 87,540 | 15 → 14 | 8 → 8 |

Calls count native CLI/hooks and any mandatory Goal-file read, separately from
RPCs. Capture/delta bytes alone are larger than quota in this cohort; the
complete-input comparison improves because quota separately requires 7,952 Goal
bytes. C1's optional expansion of all five independent peer outcomes still adds
13,940 content bytes and ten CLI/hook calls per phase. Complete clauses, exact
reconstruction, compaction recovery, tamper/early-spend rejection, one durable
writeback/debit and replay pass on both backends. These are deterministic
packaged-consumer checks, not actual model retention or outcome acceptance.
The single cohort ran alongside background work; it does not qualify latency,
model tokens/cache, billing, human cost or complete long-horizon benefit.
Historical negative warm observations remain attached to their earlier source.

The final affected integration selection passes 205 Python cases, 112 typed
cases, typecheck, configured mypy and scoped Ruff. The packaged source
still fails the unchanged historical v4 oracle (digest `d12a8843834aea84…`) at
27,934 > 13,000 characters; the earlier 26,253-character observation used a
different current-source oracle (`1b5d010a30e608c6…`) and remains separate.
Neither failure is waived or relabeled by the positive paged-input comparison.

The independently usable [#7575](https://github.com/loopx-project/loopx/pull/7575)
at `d980d621219b10353fdaeb058a1a0fd5714f4389`, based on main
`2cdff65d886d13121ffd78dccd96dd7413ab9d01`, supplies complete Goal material
through the existing opt-in native path and exact warm reuse. It has no dependency
on unmerged C1. Its main-baseline complete-Goal oracle fails on File and SQLite,
then passes with the change. Its 175 Python cases, 57 typed cases, typecheck,
configured mypy, scoped Ruff and packaged real-backend recovery/settlement pass.
Matched native stdout improves about 7.7 KB warm, but cold and loss recovery each
add about 1.2 KB and one RPC. That tradeoff stays explicit; this normal author PR
does not certify complete C1 or promote the default. C1 reuses the same predicate
and native source behavior, with scoped-wrapper reuse remaining in C1. Remaining
callers, writer cutover, historical-reader retirement and actual decision/outcome
qualification continue in the original work stream.

## Same-capture Todo-summary lane reuse

C1 subsequently rebases onto main `ffc90182bc2a896aa9980cf24dcf23a0e3276e3d`,
which includes #7575 merged as `a6283d923e3c951443fea65ba1a855c4f7c6c335`.
Its native Goal producer and integrity owner are adopted rather than copied.
Runtime `4604ee479` adds exact lane sharing in the existing typed diagnostic
owner. Equal nonempty registered arrays retain one literal owner and use
same-summary `<lane>_ref` references where smaller. Equality preserves order,
JSON types and unknown item fields; other fields and candidate collections stay
unchanged. The complete composer, action projection and native presentation use
this owner, resolving references before display caps. No extra state read,
capture, admission or settlement owner is introduced. Ambiguous, chained,
missing and cross-summary targets are rejected, including with valid inner
contract hashes. Older captures remain readable.

The same source-checkout workload emits 27,934 characters before this change
and 25,862 afterward: 2,072 fewer (7.4%), entirely in the agent summary. All other
root-section lengths are unchanged. The rebuilt wheel, with 1,889 source-matching
product members, independently fails the unchanged `d12a8843834aea84…` v4 oracle
at **25,862 > 13,000**. This is the opt-in complete-contract variant, not the
default quota output budget. Neither ceiling is raised or claimed passed.
An identical real File/SQLite producer oracle fails on immutable pre-change
`87c14a5aa` and passes with the references. The integration passes 333 Python
cases; five explicit native-Codex environment cases remain skipped. The 125 typed
cases, control-plane typecheck, configured mypy and scoped Ruff pass. A stale
bridge test is corrected separately: display edits are compared with edits,
and the resolved document retains its original semantic assertions.

The unchanged packaged harness again compares original quota `843be494…` with
this integrated candidate, in equal-length namespaces and with all mandatory
Goal reads. This new cohort measures the complete integration; its difference
does not isolate the lane change from the rebase and preceding C1 work.

| Backend / phase | Original quota stdout bytes | C1 stdout bytes | Calls, quota → C1 | RPCs, quota → C1 |
| --- | ---: | ---: | ---: | ---: |
| File / cold | 94,510 | 89,911 | 13 → 12 | 103 → 102 |
| File / warm | 57,931 | 52,094 | 9 → 8 | 99 → 98 |
| File / loss recovery | 91,720 | 87,048 | 15 → 14 | 8 → 8 |
| SQLite / cold | 94,622 | 90,638 | 13 → 14 | 103 → 103 |
| SQLite / warm | 58,043 | 52,218 | 9 → 8 | 99 → 98 |
| SQLite / loss recovery | 91,830 | 87,170 | 15 → 14 | 8 → 8 |

Both backends preserve all 24 clauses, full/delta reconstruction, original-capture
loss recovery without new admission, exact optional peer reads, tamper and early
spend refusal, and one writeback/debit under replay; work stays open. Complete
capture/delta bytes alone remain larger than quota, which separately reads
7,952 mandatory Goal bytes. Five optional peer outcomes still add 13,940 content
bytes and ten CLI/hook calls per phase. Warm RPC response bytes and loss-recovery
request/response bytes grow. One uncontrolled cohort does not qualify latency,
model retention/tokens/cache, billing, human cost or accepted outcomes. Remaining
caller replacement, writer cutover, historical-reader retirement and actual
single-session decision/outcome qualification stay open; the controller-model
comparison remains a separate later experiment.

## Mainline integration and immutable reader identity

The existing C1 proposal #7430 integrates main `447c35ed6`, including the shared
native progressive-material planner, generic capture selectors, request-scoped
settlement and current-request authorization. Those owners remain authoritative;
the integration does not create another policy or schema. The native operation
uses the existing material planner in the same typed boundary: sources needing
no material edits keep one RPC, while actual edits still apply to the original
Python JSON before capture and encoding. C1 scope fallbacks retain the complete
source. Signed complete v1 material staging remains unfinished; its full fallback
must not be reported as migrated or compacted.

Two reader seams are repaired. Generic `context-read --select` continuations
carry their selection digest through the existing CLI validator. Historical
replan commands bind the original captured complete input before diagnostic
references are expanded for the executor view; hashing that expanded view made
the original immutable capture reject a valid generated command. Both repairs
reuse the existing selector, reader and hash owners, without another admission,
receipt or debit. Tampered or differently scoped reads still reject.

The independent no-material RPC-count oracle fails on the frozen initial
integration and passes after repair. A new repeated replan-read oracle also
fails there when the complete source shares a Todo-summary lane, then passes
with the original binding. The initial repaired source `5a770d183` passes 160 typed cases and 23
real File/SQLite replan CLI cases, control-plane typecheck, configured mypy and
scoped Ruff. Existing legacy resumed-task display assertions remain alongside
the complete-input assertions; no candidate text, unknown fields or ceiling is
removed to obtain a pass.

A wider 37-module run at that revision stops at three failures, with 1,364
passed and 53 environment skips; it is not a full-suite pass. Two assertions
still read the old detached Host layout. The corrected oracle checks complete
Goal bytes and exact current work in the canonical channel, and separately
retains the original pending-read and verification assertions for the historical
reader. The fixture excludes only the harness-added `_returncode` from signed
CLI input. The third failure exposes ordinary missing Task/request identity being
classified as a wait: `281910a95` restores its contract error. The four direct
File/SQLite and missing-work regressions then pass. Its local host-preparation
extraction passes the unchanged maintainability limits; that result does not
establish execution or concurrency parity.

Integration `bca6350b2` then adopts main `ccf6aacc5`, including #7594 recovery
output guidance, #7609 first-guard choice and #7610 exact Todo-record reuse. The
existing material planner returns reference-only edits through the same native
operation, with original Python-value checks preserved. No duplicate heartbeat
view or reference owner is restored. Its 163 typed cases and control-plane
typecheck pass; focused caller/recovery follow-through is reported on #7430 at
its tested revision. Record reuse retains main's required-view profile; it does
not qualify signed-complete material staging. The source and oracle corrections
do not retroactively pass the wider interrupted/failed cohorts above.

The later 16-module cohort also stops at three failures, with 843 passed. One
new test still calls the superseded private helper signature; it is moved to the
same existing typed-plan fixture without changing its overhead assertions. The
two real File/SQLite busy-request failures expose an author refactoring mistake:
the lane decorator was left on the preparation helper rather than the complete
execution entry. The corrected entry retains the fence over admission, host and
settlement. The existing public-entry refusal oracle fails on frozen `fc4763098`
and passes after correction, along with busy-request restart/replay. Premerge
now automatically selects that existing seven-case public-entry module when
the executor, lane owner or CLI execution entry changes, including in quick
validation; nine focused planner cases cover selection and unrelated-context
exclusion. This reuses the real fence oracle instead of adding a duplicate
implementation check. Earlier automatic passes do not qualify the broken
intermediate entry, and neither stopped cohort is reported as a full pass.

This is integration qualification, not new installed or model behavior. Earlier
interrupted cohorts and the subsequent three real reader failures remain failed
historical observations. Earlier cost tables and **25,862 > 13,000** retain their
original revisions and status; no threshold is raised and no latest complete-cost
result is inferred. Caller/writer migration, historical-reader retirement and
actual single-session decisions, accepted outcomes and complete costs remain C1
work; the proposal stays unfinished.

## Signed complete material staging

The next integration adopts main `b47bbc6c9`, including first-call selection and
original captured-failure recovery. The actual native caller supplies
`complete_turn`, rather than the legacy `quota_packet` surface. After validating
the complete decision, that caller now uses the same outcome-scope, progressive
material and exact-record owners. The canonical signed capture is not edited,
re-signed or treated as a new observation. An existing
`outcome_context_projection_v0` presentation carries the original capture
reference; it is also used when material staging alone is worthwhile. Unknown
fields, unsupported obligations, unavailable planning and small material keep
the original full fallback. Delivery disabled remains unchanged.

Scope filtering can move an outcome to a different array position. The planner
therefore retains original row identity and source positions, then emits two
existing transport-edit stages: scope, followed by material edits. The Python
adapter applies those plans to its original JSON values. There is no second
Python material policy or new public work contract. The shared source-owned
guidance retains cumulative requirements, work links, current review/check
states, permissions and stop conditions; only supporting history, review
reasoning and evidence references become same-capture detail. Historical
scope-only presentations retain their original decoder.
Exact-reference equality still checks the original Python JSON types: a new
negative oracle caught integral floats being equated with integers by the
JavaScript bridge, so that existing IO-side safeguard is retained before edits.

The independent public-entry oracle requires real File/SQLite `quota should-run`
to deliver the signed staged presentation and recover the exact original source
history through `context-read --select`. It also checks unchanged binding,
original Turn receipt and CLI settlement channel. The scoped fixture deliberately
places the current Agent's outcome after a peer's outcome in the original array,
so a pointer into the filtered position cannot pass as the original evidence.
Existing full-source, disabled-delivery, missing-source, unknown-field,
small-material, pending-read and tampered-hash cases remain.

The native lifecycle cases distinguish complete required-view coverage from
optional selected evidence, a partial prefix and a truncated inner page inside
valid tool-result JSON. Partial reads never establish a full native baseline.
Repeated compaction recovers the same current presentation and original source;
it does not require finishing an unusable delta or admitting work again.
These are hook/transport checks, not proof of model retention. Default file
navigation and file-version differences are a separate active caller change;
this slice neither defines that reading policy nor adds a necessary-facts index.

Initial follow-through runs remain failed observations: the real signed caller
first exposed the unsupported `complete_turn` surface, and the historical wrapper
reader exposed a local variable-shadowing error. Author fixtures also exceeded
the existing source-summary limit and requested oversized pages without an
explicit output budget; the fixtures were corrected within the original limits.
No acceptance, page-size or cost ceiling was raised. Final validation and frozen
source sensitivity are recorded on the proposal at their tested revision.

This removes a demonstrated consumer integration gap, not the remaining C1
cutover. Earlier **25,862 > 13,000**, failed/interrupted cohorts and complete-cost
tables keep their original status. Installed adoption, model inputs/retention,
tokens/cache/billing, accepted outcomes and total latency/cost are unqualified.
Caller/writer replacement, historical-reader retirement and the original quota
single-session outcome comparison remain open; the two-session controller study
remains separate.

## Independent current-capture recovery slice

Prioritize the independently reviewable [#7628](https://github.com/loopx-project/loopx/pull/7628)
before the remaining C1 cutover. It targets main directly at
`6743ce72593121b2e15a9f8ef84355c31c94e46c`, without an unmerged C1 dependency.
The existing native adapter publishes current original recovery and pending
delivery before optional material planning. A real saved-context CLI process
exit reproduces stale predecessor recovery on main `ce4c78088` in both resume
and compaction; the same assertions pass on the proposal. Its affected cohort
has 199 passed and five existing explicit-native opt-in skips; standard premerge
passes. It remains proposed, without merge or installed-adoption qualification.

Matched project-call observations keep off/plain capture and typed-request counts
unchanged. Material staging adds one journal publication, from two to three;
capture files remain two and typed requests remain two. This buys current-source
durability, not a measured latency, model or complete-cost improvement. The same
recovery ordering is already present in this C1 integration; future mainline
integration must reuse that owner rather than restore another implementation.
