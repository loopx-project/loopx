# C1 source convergence and historical integration checkpoints

- **Date:** 2026-10-30
- **Status:** Unmerged integration evidence; C1 remains open.
- **Source:** original candidate `648104978fdd67176cccdbb765c5bc3f36bf2ca3`, integrated with target `e8f4c6f8125e3cc0ef912afac653d5542671392f`.
- **Language mirror:** [中文版](2026-10-30-c1-source-convergence.zh-CN.md)

## Current repair: scoped capture reads and migrated consumers

C1 integrates main `3a0c9608cc06`. The shared thin heartbeat renderer had turned
complete-capture availability into an unconditional pre-action whole-capture
read. Four matched real CLI cases (ordinary/App × default/explicit context-off)
compare actual main with pre-fix C1 integration `c6d7d0a573f0`: main does not add
that obligation, while C1 does.
The repaired renderer requires current work context, required materials and
applicable goals, criteria, constraints and stops; it scopes original capture
reads to decision dependencies. Explicit full-read obligations, supplied flags
and complete original-source/identity recovery remain. In all four cases, text
outside the changed read clause is byte-identical before/after. The instruction
grows by 347 UTF-8 bytes: this is a semantic correction, not a measured token or
whole-task saving.

On frozen C1 `c37bd8d0d9`, selected-work collection fails on a deleted helper;
native-child and independent-delegation checks record 62 passes and eight
failures. The existing fixtures now use the current context reader and v1
request/work-contract owner. Contradictory secondary carriers and damaged owner
identities are still deliberately injected to require pre-effect refusal;
original material, off isolation, lost-response recovery, replay and once-only
settlement assertions remain. Read-scope/source-preflight checks pass 141 cases
with four skips; native/delegation checks pass 70. A broader run first records
104 passes and 28 failures; its subsequent migrated delegation/example run
records 139 passes and two stale example assertions. After migrating explicit
selection and typed pre-effect refusal, both complete canonical-delivery cases
pass on source and the installed package. The refusal checks the original
subject and absence of Host, state, scheduler and spend effects. The broader
failures were not all independently
reproduced on untouched `c37` and are not attributed wholesale to that baseline.

The first installed-wheel run records 88 passes and 32 setup errors: the example
initializer imports TypeScript through checkout-relative paths. Its existing
Python launcher now supplies the actually loaded package root to the same
initializer; canonical authority, exclusive disposable-runtime creation and
existing-state refusal remain. No Core source is copied into the harness.
After that repair, the installed run records 148 passes and two remaining
stale settlement-field assertions; correcting them yields 150 passes in a
new complete run. All 1,815 package Python/TypeScript sources match the tested
candidate; 374 observed Python processes import only from the
isolated package. Earlier failures and partial replays keep their own evidence.

Configured cold-cache Mypy (17 roots), control-plane TypeScript and changed-path
Ruff checks pass without weakening settings. Exact-head premerge and
review/readiness remain separate qualification gates; this checkpoint does not
declare them passed.
This slice neither substantially reduces the parent PR nor qualifies complete
provider coverage, independent review, natural adoption, model costs or whole
C1. The actual main already has the intended ordinary-read scope, so this C1
regression does not justify a duplicate main change. Historical experiments and
frozen base/head cost denominators remain below.

## Previous dependency repair: source transport below the driver

C1 integrates main `5a58635028fb`, including merged proposals #8192 and #8186.
Its configured Python check originally
reported 4,562 errors in 463 files: low-level capture and journal readers imported
the driver/Host-result converter for thin source and identity helpers. Those
seven helpers now use the existing `quota.turn_envelope` transport directly;
three old private implementations are removed and published exports remain
aliases. The TypeScript decision owner, wire versions, source refusals, exact
numbers, historical recovery and once-only settlement are unchanged.

The same configured 17 roots pass with incremental checking disabled and an
isolated empty cache; strict settings and import following were not weakened.
Full control-plane TypeScript checking also passes. This is a repaired import
boundary, not a claim to fix typing in every formerly reachable module or to
improve runtime performance. The broad source run records 627 passes, six
fixture failures and four skips. A clean integrated baseline reproduces the
same six failures: fixtures access a retired second `turn_envelope` carrier.
Corrected fixtures exercise the current producer's missing-witness recovery and
deliberately injected mixed authority; all 36 module cases pass. The rebuilt
independent wheel passes 136 cases across capture/Host, session/journal,
authoring and completion paths. All 1,815 Python/TypeScript package sources
match the candidate; 141 observed Python processes import from the installed
package, without borrowing checkout source. The earlier 100-case package run
and the six original fixture failures retain their own recorded identities.

Before this dependency repair, integration reduces the parent diff from 201
files (+18,429/−2,042 at `49a621c9b8` against main `546c08c0d3`) to 197 files
(+17,928/−1,989 at `2fe98521f1` against main `5a58635028`). This is a limited
reduction; a substantially smaller review boundary remains to be delivered.

Independent review, quality/readiness, full provider coverage, natural adoption
and whole C1 acceptance remain open. This repair does not establish a new
efficiency result or qualify the whole parent PR. Historical bilingual
experiments stay together, and earlier cost measurements retain their frozen
base/head rather than becoming measurements of this integration.

## Previous extraction: complete replan authoring

Both author proposals integrate main `d0985c35aa`. [PR #8192](https://github.com/loopx-project/loopx/pull/8192)
extracts C1's existing same-response authoring owner into main, removes Python's
duplicate comparison and restores authoring for ordinary, staged and real CLI
readers. All three complete-authoring oracles fail on original main `a9485073f2`
and pass on the repair. Missing stride declarations still refuse semantic
writeback; a Host reading the delivered contract can declare its decision and
the following task consumes the independently validated plan. C1 retains its
modern source/version checks before captured reads.

The main repair passes 30 integrated source and 30 isolated-wheel cases;
C1 passes 180 focused source, 96 TypeScript and 30 isolated-wheel cases.
Canonical fixtures locate TypeScript from the loaded package. C1 fixtures use
its actual structured Host prompt and versioned capture binding, rather than
assuming an inline value or a historical digest. Earlier fixture failures remain
recorded. The main repair's frozen standard mechanical checks pass, but the
strict quality receipt remains missing. Independent review and whole C1 remain
open. This repair adds one parent RPC in the frozen fixture (2 to 3, excluding
the CLI child); it is not a net-efficiency result. Neither independent proposal
has landed, so this checkpoint does not claim the parent has already shrunk.

## Latest field reuse: a main proposal shared with C1

[PR #8186](https://github.com/loopx-project/loopx/pull/8186) isolates the native
progressive reader repair at `9df99f042b5f`, based on main `62e234c93999`.
C1 integrates that main at `2f1c91085` and the same repair at `dde48f084b`.
The existing TypeScript owner now considers an exact requirement list, a large
criterion, then its large description. Current changes and unknown controls
invalidate their containing record; current revisions and complete recovery
remain available. Complete current pages can combine with authenticated exact
inherited sections for the next read, without becoming a full-input baseline.
The native hook uses its already matched event session, rather than assuming
it inherits a shell session environment. No new admission, model invocation,
configuration or storage schema is introduced.

The following is a **main base/head** comparison, not C1's incremental gain.
One sample per File/SQLite, base/head and cold/warm/recovery/canonical-correction
condition gives 16 scenarios and 40 Turns. Twelve scenarios complete the
original synthetic three-step Task; four cold samples execute only its first
step. Current/previously delivered requirements, full UTF-8 page coverage,
independent artifacts, original identities, three receipts, single debits and
replay are checked. Setup and retry probes remain separate, with completed
child costs counted once. Product bytes at the committed head equal the
measured source.

| SQLite ordinary task | Main base | Proposed main repair | Change |
| --- | ---: | ---: | ---: |
| Warm three-step BODY | 147,059 B | 109,016 B | −25.87% |
| Corrected three-step BODY | 147,793 B | 113,819 B | −22.99% |
| Warm three-step stdout | 268,953 B | 230,730 B | −14.21% |
| Warm / corrected RPCs | 863 / 867 | 863 / 867 | unchanged |
| Warm / corrected framed RPC bytes | baseline | +2.42% / +0.98% | extra cost |

File has the same direction. Cold and this recovery cohort's BODY are unchanged.
Observed SQLite warm Python regular-file reads/writes increase 11.94%/19.06%;
correction increases them 5.73%/3.31%. Node/kernel/provider and CLI-external
fixture IO are not included. Single instrumented samples do not qualify stable
latency, model retention, natural adoption, tokens/cache, bills or net benefit.
The leaf-only prototype saved no representative task BODY; eagerly indexing
small criteria raised IO substantially. Those negatives remain evidence, not
passes attributed to the final repair.

Source regressions cover changed text/status/unknown controls, low-value splits,
partial and forged observations, compaction, off and exact recovery. The same
new case fails on untouched main. Both source and isolated-package native paths
exercise the existing owner; each actual Codex static-loopback journey takes
ten requests, includes the subsequent whole-section reuse and restores full
requirements on unverified resume. No paid model is used. At the recorded `dde48f084b` integration, C1 configured
mypy has 4,818 diagnostics; main's configured 17-file check passes. The earlier
C1 count was 4,814 at another integration head; this is not a waiver or an
attribution of the four additional diagnostics. Main standard premerge's six
direct and fourteen selected checks pass mechanically, but its strict Goal
quality receipt is missing. Independent review, CQR, unchanged-head readiness,
maintainer merge authority and whole C1 qualification remain open. This proposal
has not reduced the parent PR's size on main yet; that requires maintainer
adoption of independently reviewable slices. Earlier bilingual experiments stay
together below.

The later author integration includes main `910ff3bca5`: main product
`a8902b05b` and C1 product `7e52f3a1e`. The frozen cost table above is not
relabelled as a measurement of this later composition. Focused real-entrypoint
checks pass 102 cases on main and 104 on C1; each typed reader/report projection
set passes 66 cases, with typecheck passing. Main configured mypy passes; C1 now
reports 4,831 diagnostics in 485 files, without an attribution or waiver of the
additional diagnostics. Standard premerge passes 6/14 direct/selected checks on
main and 7/19 on C1 mechanically; both still lack the strict quality receipt,
and C1 retains its manual review hold.

## Previous integration: incremental costs after field reuse reached main

Measured product revision `b90e711cdb81` integrates main `d602248c5329`, including
#8124's field reuse, Vision audit references, first-read consumers and Chat
observation updates. C1 retains its complete-source validation in the shared
TypeScript owner; native navigation and historical-source refusals remain on
that same path. Later changes in this checkpoint are validation and documentation.
Earlier versioned cohorts below remain historical evidence.

The current matched engineering cohort uses the original synthetic three-step
Task and two canonical acceptance groups. File/SQLite × cold/warm/recovery ×
three repetitions gives 36 scenarios and 84 settled Turns. The 12 cold cases
exercise one step; the other 24 complete the original Task with matching
independently checked artifacts. Actual returned BODY is reconstructed through
EOF, and each omitted outcome must equal both its current immutable source and
its previously delivered value. Original settlement identities, three receipts,
single debits and product-source stability are checked. Preparation and deliberate
retry probes are counted separately; completed child costs are included once.

| SQLite ordinary path, three-repeat mean | Adopted main | C1 | Change |
| --- | ---: | ---: | ---: |
| Warm step 2 BODY | 27,874 B | 27,159 B | −2.57% |
| Complete three-step BODY | 102,836 B | 100,691 B | −2.09% |
| Complete three-step stdout | 225,841 B | 184,316 B | −18.39% |
| Complete three-step RPCs | 863 | 866 | +3 |
| Complete three-step framed RPC bytes | 11,754,471 B | 11,852,055 B | +0.83% |
| Observed Python regular-file read/write bytes | baseline | −10.39% / −13.83% | limited IO boundary |

File has the same direction. Recovery first resends the complete current
requirements; the three-step recovery BODY falls only 1.78%. The large savings
against pre-field-reuse main are not C1's exclusive incremental benefit.

A separate canonical-correction probe appends one criterion through the actual
refine command, preserving the original criteria. Four File/SQLite cases complete
12 Turns and four Tasks. Main conservatively rereads both outcomes; C1 reuses one
unchanged outcome at step 2 and both at step 3. The new criterion remains visible.
SQLite step-2 BODY is 52,169 → 32,905 B (−36.93%); complete-Task BODY is
149,223 → 106,688 B (−28.50%), RPCs 867 → 870 and framed bytes −0.23%.
This is one synthetic correction per provider/arm, not natural feedback adoption
or a scientific outcome result.

Final-source validation reconciles 1,065 passed / four skipped unique Python
cases, including real native Codex with static loopback inference. The 112 native
cases required focused rechecks after fixing fixture cursor planning, reading
choices before selection, and isolating the eight-page stress loop from static
resume qualification. Original failures remain recorded; this is grouped
qualification, not one wholly passing native invocation. Full TypeScript has
6,161 passed / 77 skipped; control-plane typechecking passes. The isolated wheel
matches 1,809 packaged Python/TypeScript files, passes 24 source/default/off/recovery
cases, and passes actual-client default Progressive and independent Delta checks
with no source import contamination.

Configured Python typing still fails with 4,814 diagnostics; the same configured
17 roots pass on main. This hold is not waived by broader historical typing
comparisons or the successful transport checks. PostgreSQL/full affected-provider
qualification, independent review, exact-head readiness, installed adoption and
whole-C1 acceptance remain open. BODY/stdout and Python IO are distinct metrics;
Node/kernel/provider IO, model input/cache/output tokens, invoices and sustained
latency are not qualified. No model/scientific run, live installation, CI query,
manual workflow trigger or merge was performed for this engineering cohort.

## Current private-plan boundary

New complete-source Turns now write `loopx_turn_plan_v1` with one original
`work_contract`, its source digest and capture header. The complete planner no
longer generates the v0 executor adapter. `planning_facts`, Host input and
`turnPlanWorkSource` reuse the existing TypeScript source/projection, route and
settlement owners; Python retains the persisted action-hash number codec.

| Production chain | Replacement / retained compatibility |
| --- | --- |
| Turn CLI and current launch decision → plan | Pass the complete source directly. The private planner no longer accepts a complete source paired with a v0 executor adapter. |
| Default CLI plan inspection | Retain the one complete source, including portfolio and writeback; keep inspection non-executable even when transport metadata is restored. |
| Immutable capture → Host | Bind the original source/header without regenerating an adapter; retain capture refusal, original read commands and Host v1. |
| Journal write, inspection, settlement query and CLI resume | Validate v1 through the shared source reader; preserve GoalRef, Agent, Turn, scoped identity, native Session and once-only effects. |
| Native report recovery, capability context, reward-memory activation and child-result context | Read the same work facts; carry original sparse numeric spellings with recovery proof. Existing experiment/capability gates remain required; disabled memory does no source/provider read. Host/provider and authority rules stay with their existing owners. |
| Recorded v0 journals | Keep explicit transitional source/adapter cross-check and historical adapter-only decoding; never downgrade a malformed v1 plan. |

At this private-plan checkpoint, quota presentation and the managed controller's
quota preview were still separate v0 callers; their subsequent replacement is
recorded below. Outstanding v1 work requires a compatible reader during rollback;
stopping new v1 admission does not let an older binary rewrite or resume it.
Historical readers retire only after their original work and supported recovery
window are reconciled. No new capability, setting, cache or execution grant is
introduced.

A matched replay uses the actual frozen `0eb0042191d5` implementation and the
candidate on 14 identical synthetic File/SQLite plan inputs plus 16 original
Host recovery inputs. All route/session/transaction/topology and execution
facts agree. Four plan cases use the existing no-Todo replan or request binding.
Capture publication, real journal writes, load/query and idempotent replay run
through their production owners. Only the separate capture-store namespace is
substituted for equality; source clauses and values are not normalized away.

| Measured component, aggregate over 14 plan cases | Before | Candidate |
| --- | ---: | ---: |
| Canonical plan JSON bytes after capture | 528,364 | 373,372 (−29.33%) |
| Actual journal file bytes | 698,776 | 500,880 (−28.32%) |
| Final canonical Host input bytes | 335,446 | 335,446 |

Across all 30 matched calls and their measured pipeline steps, native RPC count
is 162→158 and canonical argument/result JSON is 4,645,847→3,536,656 bytes.
Planning saves 18 calls, while checked plan loading adds 14 calls and increases
that read step from 7,052 to 340,486 bytes. Before capture, the new plan's source
digest adds 97 bytes to an unbound Host header; after capture both implementations
already carry that binding and the final model bodies agree. These are component
observations, excluding framing, CPU, model tokens, invoices, cold/warm context
costs and accepted outcomes. They do not improve the original-capture-to-selected-
body control-packet metric or establish whole-workflow net benefit.

Default plan inspection previously kept a bounded legacy view after omitting
the complete source. Removing the adapter exposed that omission, including the
portfolio and writeback context. Current inspection retains its one source.
A separate real read-only File fixture passes on both implementations with
independent stores: actual JSON stdout grows 12,388→26,524 bytes (+14,136).
This is an explicit readback cost, separate from the smaller persisted plan and
unchanged final model bodies. No CLI read-byte saving is claimed.

New no-adapter cases first fail on the frozen writer. Subsequent integration
also exposes an actual no-Todo recovery reader still using the removed adapter;
that reader now uses the complete source's original settlement identity. Invalid
current versions, missing/null sources, mixed carriers and wrong source/capture
bindings are refusal cases, alongside explicit historical/transitional controls.
After the readback, capability and historical-fixture corrections, all 403 cases
in the seven affected complete Python modules pass on the current source.
TypeScript typechecking and its full 6,053-pass/77-skip suite pass. The PR records
the exact source/wheel cohorts, retained failures/skips and remaining standard
gates. The expanded source run's 1,367 passes, 37 failures and 22 skips remain
recorded: it exposed the default inspection omission and a real reward-memory
gate still reading the absent adapter. The latter is now bound to checked work
facts; current missing-source and explicit historical controls remain distinct.
This is a private-plan migration proposal, not installed rollout, original-
quota outcome/full-cost qualification or C1 completion.

## Current quota and managed-controller cutover

The source candidate removes the remaining normal quota presentation builder
branch and changes the managed controller's fresh preview to the same complete
source used by an executing Turn. It reuses `compose_turn_decision` and
`completePlanningFacts`; the latter validates and derives work/lineage inside
the existing `turn.controller` RPC. Exact numeric tokens cross that boundary
without another projection RPC. Unknown supporting facts survive; an unknown
current execution action is refused before dispatch or settlement.

| Replaced mechanism / caller | Shared owner and actual deletion | Remaining consumer and exit condition |
| --- | --- | --- |
| Quota `_project_quota_cli_payload` and renderer selection | Delete the v0 builder import, ordinary/envelope format branch, its soft fallback and unused scheduler/capture arguments. Existing complete composition and renderer own should-run output. | Status, plan and monitor remain diagnostic projections. The request owner already permits `--turn-envelope` only on should-run; that alias selects no second current contract. No byte saving is credited to deleting unreachable branches. |
| Managed fresh quota preview → loop disposition | Compose the full source with the actual scheduler context; remove `TurnControllerInput`, its private JSON copy, `build_turn_controller_input` and the TypeScript `build_controller_input` operation. Reuse checked complete planning facts for disposition. | The managed controller keeps its observation/retry and journal ownership. Historical v0 decisions alone retain their scalar route codec. |
| Complete plan/capture/journal → reader and resume | Retain the preceding single-source v1 migration and its original source/header checks. Normal preview no longer rebuilds its v0 adapter. | Recorded v0 and transitional journals retain explicit readers until their outstanding work and supported recovery/rollback window are reconciled. Malformed v1 never falls back to them. |
| Explicit legacy envelope builder and historical renderer | No new ordinary product caller remains for the legacy builder. Complete rendering still reuses the existing renderer module. | `control_plane_composition_scenarios`, `model_behavior_corpus` and original-version controls still construct legacy inputs. Retire a legacy constructor/decoder only after the last supported fixture/replay caller and version window migrate; preserve independent historical negative coverage. |

A reproducible maintenance drill temporarily adds one missing synthetic-material
check to the existing complete-source validator, then restores the owner file
byte-for-byte. The same four real CLI/provider cases run against integrated
baseline `856faed423620` and the candidate: File/SQLite quota refusals pass on
both; the two managed refusals fail on baseline and pass on candidate. One
production owner edit and zero caller edits now reach both entries; the former
managed path needs a separate migration. The temporary check is not shipped as
a new production read gate. This proves that maintenance boundary, not natural
model compliance or Goal acceptance.

The durable eight-case source/unknown-action matrix likewise has four managed
failures and four quota controls passing on that baseline, then eight passes
on candidate. It checks actual isolated providers, original supporting evidence
including unsafe integers/integral floats, no journal mutation and no debit.
The affected source cohort passes 408 Python cases and 76 focused TypeScript
cases. Twenty-two stale integration-fixture failures were reproduced on the
unchanged baseline before correction: retry injection targeted a retired helper
statement, complete planning used a newer operation, sender validation moved
earlier, and historical controls had incorrectly used current v1 producers.
Corrections retain receiver refusal, original-version and retry negatives;
those historical failures are not erased or counted as candidate regressions.

After integrating main `d4aaaaefbd37`, the expanded source cohort passes 427
cases, including the public lane fence and complete journal recovery. Integration
retains main's typed casts together with complete-source Goal/Agent checks; an
initial missing `cast` import was exposed by the lane check and repaired. The
isolated installed wheel passes the eight-case matrix and two native synthetic
transactions plus once-only replay on each of File/SQLite. Package provenance
is checked independently of source-only test helpers. A stale frontend bundle
initially held wheel building; rebuilding the existing bundle restored it.

Standard premerge still has a maintainability hold and no exact-scope quality
receipt. A real Git checkout of the same main reproduces four module-metric
findings; the candidate additionally exceeds the existing quota-emitter
decision-function limit at 62 decision points/74 statements. No ceiling or
exception is raised here. The remaining original size holds likewise remain
failures; passing the focused cohort is not a passing whole-PR merge gate.

This remains an unmerged source proposal. No candidate installation, default
change, model run, budget relaxation or C1 closure follows from these checks.
The original-quota cost comparison below is separate from the earlier adjacent
plan/journal savings and from the still-open natural accepted-outcome gate.

### Original-quota product-path cost observation

The frozen original-main baseline is `5f1a9d78ba7ac`, not the adjacent private-plan
baseline. Candidate runtime content is pinned to `b8c2bf1dc`, before the later
mainline integration; later source validation does not remeasure this cohort.
Both snapshots use Python 3.12.15, the same frozen numbered-artifact
task, isolated File/SQLite stores, equal-width namespaces and native context
delivery. Each of twelve scenarios reads the returned entry and every required
continuation, creates the same artifact, passes its independent validator,
writes back, spends once and verifies an idempotent spend replay. Artifact
SHA-256 agrees in all twelve cases. This is synthetic acceptance of that small
task; it does not establish equal natural-model outcomes or equal decision
obligations across the two packet versions.

Cold starts an isolated runtime. Warm includes its preparatory guard/read;
post-compaction additionally runs the actual PreCompact/PostCompact CLI hooks
before the next admission. The latter measures baseline invalidation, not a
resume of interrupted business effects. Setup is excluded. Preparation failures
remain recorded separately; the table uses the completed final comparison
cohort only.

The primary metric is `1 - selected BODY file bytes / same full capture bytes`.
It excludes reader envelopes and other output; those are counted separately.

| Provider / scenario | Original quota: capture → BODY (reduction) | Candidate: capture → BODY (reduction) |
| --- | ---: | ---: |
| File / cold | 59,033 → 28,446 (51.81%) | 55,416 → 55,416 (0%) |
| File / warm | 58,464 → 6,419 (89.02%) | 54,849 → 9,411 (82.84%) |
| File / post-compaction | 58,582 → 28,562 (51.24%) | 54,963 → 54,963 (0%) |
| SQLite / cold | 59,121 → 28,530 (51.74%) | 55,500 → 55,500 (0%) |
| SQLite / warm | 58,552 → 6,344 (89.17%) | 54,937 → 9,397 (82.89%) |
| SQLite / post-compaction | 58,674 → 28,648 (51.17%) | 55,055 → 55,055 (0%) |

Candidate full fallback retains the full source; its 0% BODY reduction does not
mean zero net benefit. Actual initial reads select their required material
instead of printing that entire BODY. Conversely, a smaller warm delta does
not guarantee a cheaper initial read: the candidate's current initial read
still supplies complete required context.

| Provider / scenario | All stdout bytes, original → candidate | RPC calls, original → candidate | Framed socket bytes, original → candidate |
| --- | ---: | ---: | ---: |
| File / cold | 55,815 → 48,709 | 236 → 237 | 1,454,525 → 1,547,159 |
| File / warm | 67,522 → 76,353 | 325 → 327 | 2,128,926 → 2,348,959 |
| File / post-compaction | 89,968 → 75,700 | 327 → 329 | 2,132,977 → 2,343,467 |
| SQLite / cold | 56,212 → 49,086 | 236 → 237 | 1,456,277 → 1,548,893 |
| SQLite / warm | 67,849 → 76,806 | 325 → 327 | 2,131,180 → 2,351,673 |
| SQLite / post-compaction | 90,461 → 76,149 | 327 → 329 | 2,135,847 → 2,346,351 |

All stdout includes guard headers, required reader pages, preparation/hooks,
writeback and spend/replay responses. Both versions use 5/7/9 CLI processes
and one independent validator for cold/warm/post-compaction; there are no help
or failed-command retries inside this final cohort. Readiness pings and framing
are included in RPC counts/bytes; neither side uses referenced RPC snapshots.

One-time preparation must not be charged again for every steady warm step.
Separating that measured preparation, the current warm operation adds
15,936 File / 16,083 SQLite stdout bytes, while a cold operation saves
7,106 / 7,126. One measured cold byte saving would not pay for one measured
warm increment on this workload. This is conditional byte accounting, not a
prediction of a real session's event mix, tokens, invoices or outcome value.

Capture IO also grows. Observed Python capture reads/writes on File are
186,399/189,228 → 277,768/238,234 bytes cold,
387,693/392,774 → 664,229/495,634 warm, and
430,659/378,418 → 664,963/475,336 post-compaction. SQLite shows the same
direction. Observed journal bytes and retained provider-state footprint agree
within each matched scenario; retained capture footprint grows by
24,503–51,506 bytes. These are Python regular-file traffic and retained disk
sizes, not physical IO or write amplification. Node/provider IO and native
SQLite page IO remain unmeasured. Process wall time includes Python startup
and wrappers; one observation per condition does not qualify latency. No model
requests, cache/invoice coverage or human-attention cost is measured here.
The main-path byte/RPC regressions and the original size holds remain open;
adjacent plan/journal savings cannot offset them by declaration.

### Prepared initial-reader transport

The subsequent reader increment is compared against integrated `86081b142c62`,
not against the original-quota cohort above. Capture generation already asks
the TypeScript selection owner for current initial navigation. It now pins that
owner's resolved addresses with the existing immutable selection cursor, which
the opaque first reader reuses. The reader still checks the complete source
and selected-view digests on every page; selected values come from the original
Python JSON bytes, including unsafe integers. Unselected prose no longer crosses
the runtime bridge a second time. The raw `--initial-read` recipe, explicit
readers and complete recovery remain available. Optional metadata write failure
keeps the raw recipe; an unsupported historical schema uses generic discovery
with one additional bounded selection RPC. A startup/transport failure does
not retry the runtime.

The independent same-capture oracle preserves the exact selected body and its
unknown obligations. No additional BODY compression or model-input saving is
credited to this change. In particular, warm initial reads still need current
required facts; an old delta or saved cursor grants no retention or freshness.

The same numbered-artifact fixture, Python version, providers, preparation,
compaction hooks, validator and once-only settlement are replayed in twelve
isolated cases. All artifacts agree. Full-path socket traffic falls modestly;
RPC counts remain 237 / 327 / 329 for cold / warm / post-compaction on both
versions, including pings.

| Provider / scenario | All stdout bytes, base → candidate | Framed socket bytes, base → candidate |
| --- | ---: | ---: |
| File / cold | 48,258 → 48,293 | 1,541,169 → 1,509,780 |
| File / warm | 75,583 → 75,664 | 2,337,709 → 2,275,365 |
| File / post-compaction | 74,963 → 75,033 | 2,333,325 → 2,270,641 |
| SQLite / cold | 48,635 → 48,670 | 1,542,959 → 1,511,454 |
| SQLite / warm | 76,036 → 76,117 | 2,341,764 → 2,276,882 |
| SQLite / post-compaction | 75,412 → 75,482 | 2,336,165 → 2,273,331 |

Current initial-reader stdout is byte-identical in each pair; its socket traffic
falls from approximately 60 KB to 30 KB. The longer cursor path adds 35 / 81 /
70 stdout bytes across the complete cold / warm / post-compaction flows.
SQLite Python-observed capture read/write bytes are
276,540/236,976 → 280,522/237,574 cold,
661,271/492,992 → 669,177/490,960 warm, and
662,043/472,836 → 669,965/474,028 post-compaction. File shows the same increased
read cost; visible journal bytes agree. Cursor preparation adds local work and
retained metadata. Native provider IO, model/cache invoices and natural outcomes
remain unmeasured; single wall-clock observations do not qualify latency.
Neither the original-quota warm regression nor the existing size/quality holds
is cleared by this adjacent transport improvement.

The same-capture transport regression fails on the frozen base while its raw
reader control passes. The initial eleven-module source run records 647 passes,
78 skips and four failures: two navigation tests still invoked the now-prepared
model cursor as a fresh recipe, and two historical staged-reader fixtures also
fail on the unchanged base. Their explicit raw/historical controls are retained;
the final three affected modules pass 103 cases. The actual installed wheel
passes the same 103 cases, with installed module and CLI-child routing checked.
An initial wheel attempt omitted the repository runtime-isolation fixtures and
is rejected as evidence. The default-route fixture now refuses before writing
unless both roots belong to its disposable test base; the repaired package
qualification loads the original isolation fixtures. Source isolation checks
pass 21 cases and a missing-isolation negative leaves synthetic roots untouched.
Standard premerge passes seven direct checks and eighteen of nineteen selected
checks; the same five maintainability findings remain a hold. No ceiling,
exception, merge authority or natural-outcome acceptance is changed.

### Native command ownership and settled receipts

The next normal native caller adopts the already shipped
`refresh-state --settle-turn` adapter. Its complete-source plan is
`quota_settlement_plan_v2`: writeback invokes that adapter with explicit receipt
output, and quota spend is runtime-owned rather than a second unconditional
Agent command. Completion, claim/lease, original identity, Vision authoring and
terminal conditions retain their existing obligations. A partial failure still
returns the canonical original `settlement_owed`; there is no extra admission,
automatic retry or direct-debit bypass. Native-off/manual v1, historical readers,
replan successor creation and callback-owned runners retain their paths.

Receipt output is an explicitly selected post-effect result projection, not a
new admission input or a context-retention claim. Exact receipts and unresolved,
failed or unknown controls remain visible. Known supporting snapshots remain
complete in an immutable result with the originating CLI reader. Failed capture
delivery preserves committed receipts and permits exact-identity retry. A real
replay test also exposed that the old combined adapter returned only the first
two receipt steps on refresh replay despite reporting settled state. Replay now
independently reads all three original receipts without executing another child
or debit. Existing quota presentation IO is separated within its existing owner.

Three implementations run the same frozen numbered-artifact workload on Python
3.12.15: original quota `5f1a9d78ba7a`, integrated C1 `898aaa9d0c33`, and this
runtime increment. Eighteen isolated File/SQLite cold, warm and post-compaction
cases produce identical independently checked artifacts and one debit each.
The child CLI's RPC and Python IO are included, using an import observer that
waits for the pinned package rather than importing LoopX from its working
directory. Agent CLI calls fall 5/7/9 → 4/6/8; one internal CLI child means total
CLI process counts remain 5/7/9. RPC counts include pings and that child.

| Provider / scenario | All stdout B: original / integrated C1 / increment | RPC: original / integrated C1 / increment | Socket B: original → increment |
| --- | ---: | ---: | ---: |
| File / cold | 55,622 / 48,556 / 39,643 | 236 / 237 / 231 | 1,452,338 → 1,577,844 |
| File / warm | 67,302 / 76,098 / 66,385 | 325 / 327 / 321 | 2,125,470 → 2,350,007 |
| File / post-compaction | 89,643 / 75,454 / 65,676 | 327 / 329 / 323 | 2,129,249 → 2,345,468 |
| SQLite / cold | 56,020 / 48,933 / 39,982 | 236 / 237 / 231 | 1,454,093 → 1,579,746 |
| SQLite / warm | 67,723 / 76,551 / 66,792 | 325 / 327 / 321 | 2,128,276 → 2,352,873 |
| SQLite / post-compaction | 90,133 / 75,903 / 66,079 | 327 / 329 / 323 | 2,132,085 → 2,348,364 |

The primary metric remains bytes from each complete capture to its own selected
BODY file. These are separate from initial-read pages and all stdout above.

| Provider / scenario | Original F → BODY B | Increment F → BODY B |
| --- | ---: | ---: |
| File / cold | 58,907 → 28,329 | 54,498 → 54,498 |
| File / warm | 58,338 → 6,408 | 53,925 → 8,605 |
| File / post-compaction | 58,458 → 28,446 | 54,027 → 54,027 |
| SQLite / cold | 58,995 → 28,414 | 54,578 → 54,578 |
| SQLite / warm | 58,426 → 6,420 | 54,005 → 8,613 |
| SQLite / post-compaction | 58,546 → 28,530 | 54,107 → 54,107 |

Warm same-capture BODY reduction is 84.04% / 84.05%, against original
89.02% / 89.01%. The candidate's cold and post-compaction rates remain 0%.

Full BODY fallback remains 0% reduction, not zero net value. More importantly,
removing one-time preparation still leaves the current warm step at
33,603 → 40,558 B (File) and 33,932 → 40,901 B (SQLite), about 7 KB above original
quota. The approximately 1.4% improvement in warm *total* stdout includes the
smaller one-time preparation and does not clear that recurring regression.
Warm Python-observed CLI reads/writes are
636,750/451,370 → 919,810/591,486 B (File) and
645,056/454,206 → 928,728/595,466 B (SQLite). Node/provider/native page IO, model
usage/cache/invoices and natural outcomes remain unknown. Concurrent byte
characterization does not qualify latency. Validator calls/output are counted; its file IO is not measured. The final-head
repetition changes warm BODY from 7,799/7,807 to 8,605/8,613 B while stdout is
identical; each capture retains its own denominator, not a stable-ratio promise.
Whole-workflow net benefit and C1 completion remain open; the remaining targets are repeated warm control input
and duplicate snapshot/transport IO, not another runner or a weaker obligation.

The integrated runtime at `5aa46dc27de8` passes all ten affected Python modules
(419 passes, 10 skips), TypeScript typechecking and 243 focused TypeScript tests.
Its actual isolated wheel passes 245 cases with 10 skips; 1,181 loaded Core
modules and 392 CLI child routes resolve to that installation. Earlier fixture
failures remain recorded: stale v0 assertions, a metadata-length-dependent
UTF-8 offset and an implicit producing working directory were corrected using
their independent current semantics. Failed result capture, oversized unknown
controls, wrong bindings/workspaces, lost responses and exact replay have real
CLI coverage. Standard premerge reports seven direct checks and nineteen
selected checks, with one inherited maintainability advisory. The raw ratchet
still fails on four untouched baseline modules; the former quota-emitter
finding is absent. No ceiling, exception or historical failed result changes;
this is neither an exact-scope quality receipt nor merge/readiness authority.

### Settled snapshot replay and repeated original-quota comparison

The output-IO increment at `4ef428767ce9` treats a completed `current` Todo
projection like its already supported `delivered` state. Strictly known inactive
runtime-route, shared-projection and Explore snapshots also use the existing
immutable result reader. No additional RPC, cache, permission owner or setting
is introduced. This is Python file/output IO over existing TypeScript facts;
settlement, freshness and permissions still belong to their typed owners.
Unsettled/error/dry-run results, unknown nested fields, route conflicts,
projection retries, copied artifacts and blocking postconditions remain inline.
The original identity, three receipts and unsatisfied Vision checkpoint remain
visible; reading the full stored result proves neither acceptance nor retention.

A new matched cohort freezes main `f04ec308f6e7` and this candidate, with the
same numbered-artifact Task, necessary materials, independent check and one
debit. Three repetitions across File/SQLite and cold/warm/post-compaction give
36 cases and 252 verified parent/child source/executable observations. Table
values are medians of the three observations, in bytes; `current` excludes
one-time baseline preparation. Preparatory output remains in `all stdout`.

| Provider / scenario | All stdout B: main → candidate | Current stdout B: main → candidate | RPC | Framed socket B |
| --- | ---: | ---: | ---: | ---: |
| File / cold | 55,570 → 36,270 | 55,570 → 36,270 | 236 → 203 | 1,451,832 → 1,437,675 |
| File / warm | 67,892 → 62,991 | 34,239 → 37,183 | 326 → 284 | 2,127,817 → 2,152,912 |
| File / recovery | 89,549 → 62,276 | 55,764 → 36,392 | 328 → 286 | 2,131,374 → 2,147,978 |
| Sqlite / cold | 55,963 → 36,344 | 55,963 → 36,344 | 236 → 203 | 1,453,556 → 1,439,471 |
| Sqlite / warm | 68,313 → 63,133 | 34,568 → 37,261 | 326 → 284 | 2,130,625 → 2,155,458 |
| Sqlite / recovery | 90,038 → 62,414 | 56,165 → 36,466 | 328 → 286 | 2,134,204 → 2,150,686 |

The recurring warm step is still **8.60% / 7.79% larger** (File/SQLite), while
RPC count is 12.88% lower and framed bytes are 1.18% / 1.17% higher. Warm
Python-observed reads+writes are 787,482 → 813,035 B and
796,984 → 822,778 B (medians of each direction summed), about 3.2% higher.
Counts and byte observations do not qualify CPU/latency while other validation
runs concurrently; native provider/SQLite-page IO and real model cache, input,
generated output, invoices and natural effectiveness remain unknown.

This increment changes settlement output, not guard BODY selection. In the
first repeat, warm complete-capture F → selected BODY is
58,306 → 7,082 B versus 53,899 → 8,579 B (File), and
58,396 → 7,094 B versus 53,979 → 8,609 B (SQLite). Each rate uses its own
same-capture UTF-8 files. Candidate cold/post-compaction BODY is full, so its
primary reduction remains 0%; that does not imply zero workflow value. Warm
candidate rates vary 82.62–84.08% / 82.64–85.44% across these repetitions,
with no new BODY saving attributable to this output-only change.

A separate component comparison applies the old `9380173c` and new output IO
to identical captured settled payloads. Warm first-result plus replay saves
3,347 / 3,612 B. If a consumer actually requests all newly deferred fields
from both results, the real selected readers add 5,434 / 5,723 B and two RPCs:
that component instead costs about 2.1 KB more. An equal-demand-probability
byte-only proxy breaks even near 62% / 63%; demand is unmeasured, and neither
that proxy nor this fixed Task establishes model/billing net benefit. There is
no new mandatory detail-read gate.

Seven complete affected Python modules pass 360 cases; the isolated actual
wheel passes 241 cases using the original test configuration/conftest and
default-root isolation. All 1,185 loaded parent Core modules, 1,186 child CLI
routes and 1,199 Core-loading child import audits resolve to that wheel.
Real File/SQLite caller tests recover the full result, preserve one debit and
all receipts on replay, and keep future or actionable nested controls visible.
These are source/packaged output qualifications, not installed adoption or
whole C1 acceptance. Historical cohorts, failures and frozen budgets above
remain intact. The remaining recurring warm-input and total-cost gap is open.

### Composed Vision budget recovery

The existing Vision prepare owner now returns independent numeric budget
diagnostics together through the shared bounded request-error transport.
C1 consumes the same rule through its complete capture and returned composed
settlement command. Five independent numeric failures cause one refusal;
oversized enum text retains its budget error, while a short invalid enum still
rejects. No limits increase, authored-packet rewriting or new decision owner is
introduced. File/SQLite tests preserve the source state and run index on every
refusal. Explicit correction, independent history readback and replay retain
the complete packet, original Turn, three receipts and exactly one debit.

A fixed caller compares the frozen preceding C1 head `75c05340b6ce` with the
integrated product code. Both sides correct only fields actually named in the
error, reach the same final packet and settle once without another guard.
Equal-width namespaces and the same Python isolate this retry segment. The
accounting child is included: seven Python processes before, three after, per
provider. Equal fixture setup, admission and independent verification are
outside both measured segments.

| Provider | CLI attempts | Stdout B | RPC | Framed socket B | Observed Python read B | Observed Python write B |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| File | 6 → 2 | 11,473 → 9,269 | 137 → 133 | 822,570 → 817,482 | 131,460 → 116,843 | 39,169 → 39,193 |
| SQLite | 6 → 2 | 11,499 → 9,279 | 137 → 133 | 823,294 → 818,206 | 131,962 → 117,297 | 39,285 → 39,309 |

The first rejection is larger; the complete retry segment emits about 19.2%
less stdout, uses 2.92% fewer RPCs and about 0.62% fewer framed bytes. Observed
Python reads fall about 11.1%; writes increase by 24 B. Native provider IO,
latency, real model corrections, input/cache/generated output and billing are
not measured. This is a deterministic error-recovery improvement relative to
the preceding C1 head, not a new comparison with latest main, a new same-capture
BODY reduction or a qualification of the original quota workload's overall net
benefit. Its warm-input regression and all frozen holds above remain open.

### Native ready handoff on the complete contract

This integration includes main `bd79adc64968` and the existing Host ready-source
handoff. The unchanged handoff forced its ready recheck to `context-delivery
off`; inside C1 that replaced the enabled complete composer's combined
settlement with the older split plan. A new independent assertion fails in all
four real File/SQLite × App/SSH paths before the correction. The thin adapter
now preserves the enabled native profile and uses the existing deferred-display
option. It captures the complete ready source before the existing saved-capture
owner presents it once. No new flag, semantic owner, RPC or authority is added.

| Actual caller | Replaced behavior | Shared owner and remaining boundary |
| --- | --- | --- |
| Bound native execution preparation | Forced-off ready recheck | Existing complete composer retains mandatory context and combined settlement; Python only forwards the native directory/display mode |
| Host post-tool context | Model must recover the missing ready contract with another guard | Existing capture/reader publishes the verified same-Turn source and original claim reference |
| Prepared native closeout | Active prepared caller receives the split v1 plan | Existing v2 settlement owner supplies the combined operation and three receipts; old records and unbound/off callers retain their original recovery contracts |

The complete Goal is checked against the independent original text, inline or
through an actually declared read. Off remains inert; identity mismatch, corrupt
source, replay and once-only accounting checks remain. This fixes consumer
adoption, not the separate requested semantic-rule-change exercise or the final
historical-reader retirement. Those C1 acceptance boundaries remain open.

Seven complete affected source modules and the actual isolated wheel each pass
280 cases, with 70 existing opt-in skips. The wheel's 1,897 compared product
files match; 235 completed processes and 44,758 Core-module observations show
no checkout-origin violation. Abrupt exits remain outside exit-time auditing.
The initial source run retains 279 passes, one stale reader-program assertion
failure and 70 skips; the existing reader-program correction is reused, and its
whole 20-case module passes before the final full run. Failed caller pilots
requested the wrong legacy view or reused a capture directory; their failures
are retained, not counted as completed comparisons or fixed by relaxing capture
immutability. No active App/CLI installation is claimed.

The new hard-lease cohort uses the frozen public numbered Task, stopping at
step three. Cold completes step one; warm/recovery first validate and settle
step one, release its lease, then admit and validate step two. Recovery runs
the actual PreCompact/PostCompact hooks before that second Turn. Both arms use
the original quota entrypoint, identical material, equal-width namespaces, one
Python and one observer, with each parent/child importing its pinned product.
This is separate from the historical soft-claim 36-condition cohort below.

Three repeats across both providers, scenarios and arms give 36 conditions and
60 independently checked work Turns. Each reaches the same artifact within its
scenario, three committed receipts and one debit per Turn, including replay.
All 654 parent/completed-child source observations and 640,896 module origins
match their arm; product source hashes remain unchanged. Parent RPC totals
already include children. Whole columns include the completed preparation work;
current columns exclude it but retain compaction hooks. Tool output, all
necessary reads, Host execution, writeback, spend, readback and lease release
are included. Values are three-observation medians:

| Provider / scenario | Whole stdout B | Current stdout B | RPCs | Framed RPC B | Python fixture read B | Python fixture write B |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| file / cold | 57,597 → 45,996 | 57,597 → 45,996 | 423 → 288 | 2,938,704 → 1,929,733 | 720,046 → 662,664 | 302,925 → 272,952 |
| file / warm | 118,430 → 95,236 | 60,833 → 49,240 | 843 → 578 | 6,271,394 → 4,130,344 | 1,926,283 → 1,700,979 | 675,761 → 593,384 |
| file / recovery | 118,202 → 94,776 | 60,269 → 48,556 | 845 → 580 | 6,259,244 → 4,112,058 | 1,942,194 → 1,715,504 | 656,353 → 573,186 |
| sqlite / cold | 58,016 → 46,112 | 58,016 → 46,112 | 423 → 288 | 2,943,792 → 1,932,217 | 737,286 → 677,202 | 304,783 → 275,281 |
| sqlite / warm | 119,272 → 95,472 | 61,256 → 49,360 | 843 → 578 | 6,281,728 → 4,136,666 | 1,965,207 → 1,734,459 | 679,477 → 598,310 |
| sqlite / recovery | 119,040 → 95,008 | 60,688 → 48,672 | 845 → 580 | 6,269,816 → 4,117,784 | 1,981,156 → 1,748,980 | 660,103 → 577,919 |

Warm whole stdout falls 19.58% / 19.95%, RPCs 31.44%, framed bytes 34.14% /
34.15%, and observed fixture Python read-plus-write bytes about 11.8%. Current
step stdout falls 19.06% / 19.42%. These are cumulative C1 comparisons, not
attribution of every reduction to this small integration correction. The warm
wire ranges are file: main 6,271,290–6,271,572; candidate 4,130,308–4,130,664; sqlite: main 6,281,492–6,281,936; candidate 4,136,236–4,136,704. No statistical significance is claimed.

The primary metric uses each producer's original complete `decision.json`, not
an already projected recovery document. All 60 producer/BODY pairs verify the
original Turn and controls plus published recovery/selected digests:

| Provider / scenario | Main producer F → BODY B (reduction) | Candidate producer F → BODY B (reduction) |
| --- | ---: | ---: |
| file / cold | 60,084 → 27,993 (53.41%) | 53,873 → 27,291 (49.34%) |
| file / warm | 75,126 → 28,022 (62.70%) | 61,271 → 27,230 (55.56%) |
| file / recovery | 75,342 → 28,135 (62.66%) | 61,465 → 27,376 (55.46%) |
| sqlite / cold | 60,310 → 28,201 (53.24%) | 54,091 → 27,436 (49.28%) |
| sqlite / warm | 75,352 → 28,195 (62.58%) | 61,491 → 27,434 (55.39%) |
| sqlite / recovery | 75,568 → 28,319 (62.53%) | 61,683 → 27,530 (55.37%) |

Candidate warm BODY is smaller in absolute bytes, while its reduction percentage
is lower because its complete producer source is also smaller. Neither ratio
replaces the complete caller comparison. File-backed delta does not establish
model retention. Referenced RPC snapshot payload bytes are observed as zero in
this cohort; provider/native/SQLite-page and full-process IO remain unmeasured.
CPU/latency, actual model input/cache/generated output, fees, natural adoption
and long-horizon net benefit remain unqualified. All earlier positive/negative
cohorts, frozen budgets and original outcome requirements retain their versions;
this source/package result is neither merge readiness nor complete C1 acceptance.

### Executable required reads and native-runner attribution

A current complete source now validates explicitly executable required reads in
`assertInteractionRequiredReads`, beside the existing interaction owner. A read
that declares a command or ordering must retain a non-empty, NUL-free command;
its explicit ordering must be the registered `before_work`. Prose-only
obligations, unknown supporting metadata, quoted Unicode commands and an omitted
historical ordering remain unchanged. A valid unread obligation stays pending:
this check neither acknowledges reading nor creates a blanket material-read gate.

| Changed boundary | Actual owner / caller change |
| --- | --- |
| Executable read semantics | One TypeScript interaction validator; one call from the existing complete-source validator. Quota, managed preview, planning and Host consumers retain their existing calls. No Python rule or extra RPC is added. |
| Unsupported direct complete source | The Python transport preserves an explicit `contract_binding`, including an unsupported version, for the typed planning reader instead of trying the legacy adapter. |
| Qualification callers | Move stale CLI fault injection from the removed envelope builder to actual complete composition; preserve explicit historical version negatives. Use real canonical hard-lease inventory and the actual artifact workspace instead of mocked admission or incidental Git checkout discovery. |

On unchanged `f1614358ac13`, the eight new File/SQLite quota/managed refusal
cases fail while eight existing valid/unknown-action controls pass. The shared
rule reaches both entries without changing either caller. A first implementation
also rejects a legitimate prose-only obligation; its original full-text test
exposes that mistake, and the implementation is corrected without removing that
assertion. A separate real Turn CLI case checks unsupported version/codec and a
correctly hashed unsupported read order before Host launch or debit. This is a
bounded semantic-rule change, not independent review or proof of model compliance.
Registry hook admission and recorded-version readers retain their distinct owners;
a new registered ordering would still require that protocol's own review.

After integrating main `92294e7245cb`, the final eight complete Python modules
pass 196 cases on both source and the actual isolated wheel; the wheel has 1,897
byte-matching packaged source files, 325 completed-process and 103,615 Core-module
origin observations with no checkout contamination. Abrupt process exits are
outside that exit audit. The six relevant TypeScript modules pass 121 cases,
and full control-plane
typechecking and changed-file Ruff pass. The earlier broad 178-pass/7-failure
source and 168-pass/17-failure wheel runs remain recorded. Corrections preserve
the original assertions: actual composition injection replaces six stale
constructor hooks; the frontier fixture uses canonical authority; wheel helpers
are copied explicitly, and writeback names the real artifact workspace.
Repository worker scripts remain explicit test callers; the origin audit qualifies
packaged Core, not a separately installed scheduler. The ordinary bundle is
rebuilt before packaging; no candidate is installed.

The earlier 20,533-character premerge failure now has a matched native-runner
counterexample on both immutable main `bd79adc64968` and candidate
`f1614358ac13`. With one parent root, direct output is 20,333 characters and the
native runner is 20,501: its isolation component adds 21 characters to eight
fixture-root occurrences (168 total). The original premerge layout adds another
four-character directory, producing 20,533 on both revisions. After substituting
only the owned fixture root and observation timestamps, payload facts agree.
Every original assertion runs against the original 20,500 ceiling; neither paths
nor output are shortened for those measurements. This character-budget diagnosis
is separate from the UTF-8 complete-capture-to-BODY metric.

[The existing fixture-calibration proposal](https://github.com/loopx-project/loopx/pull/8069)
owns that separate budget question. No calibration, skip or waiver is included
here; the failed premerge observation remains a failure. The preceding matched
cost cohorts keep their exact measured revisions and are not relabeled as a
measurement of this added validation. CPU, model usage, natural adoption and
long-horizon net benefit remain unknown.

The legacy constructor's remaining in-tree calls are the explicit composition
and model-behavior qualification fixtures. Original-version journal/recovery
readers still need their supported work/window reconciliation; none is removed
just to make a retirement count. This slice neither retires those readers nor
closes the complete C1 acceptance.

### Latest-main matched hard-lease comparison

A separate repetition freezes main `92294e7245cb` against candidate
`b3b3b232724d`, including the executable-read validation above. The original
numbered Task, fixture, observer, shared Python and cold/warm/compaction-recovery
protocol are unchanged. Three repeats per arm/provider again produce 36
conditions and 60 work Turns. All 1,914 product source hashes per arm remain
unchanged; 654 parent/completed-child observations and 640,896 Core-module
origins match their source. The complete original Task appears in each actual
consumed read. Every Turn keeps its original identity, three committed receipts,
one debit and the same accepted artifact for its scenario. This is engineering
qualification, without a model or an installed App rollout.

The complete workflow includes the completed preparation Turn, all required
reads, native Host preparation, compaction hooks where applicable, writeback,
spend, replay/readback and lease release. Parent totals already include children.
The current-step column excludes completed preparation. Three-sample medians:

| Provider / scenario | Whole stdout B | Current stdout B | RPCs | Framed RPC B | Python fixture read B | Python fixture write B |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| file / cold | 57,221 → 45,732 | 57,221 → 45,732 | 423 → 288 | 2,930,378 → 1,924,529 | 715,582 → 658,504 | 301,509 → 271,509 |
| file / warm | 117,662 → 94,692 | 60,441 → 48,960 | 843 → 578 | 6,254,008 → 4,120,060 | 1,916,435 → 1,691,969 | 672,788 → 590,508 |
| file / recovery | 117,450 → 94,248 | 59,893 → 48,292 | 845 → 580 | 6,241,950 → 4,101,350 | 1,932,262 → 1,706,384 | 653,451 → 570,267 |
| sqlite / cold | 57,640 → 45,848 | 57,640 → 45,848 | 423 → 288 | 2,935,504 → 1,927,053 | 732,814 → 673,042 | 303,374 → 273,849 |
| sqlite / warm | 118,504 → 94,928 | 60,864 → 49,080 | 843 → 578 | 6,264,426 → 4,125,624 | 1,955,379 → 1,725,427 | 676,528 → 595,288 |
| sqlite / recovery | 118,288 → 94,480 | 60,312 → 48,408 | 845 → 580 | 6,252,476 → 4,107,176 | 1,971,260 → 1,739,860 | 657,195 → 575,088 |

Warm whole stdout falls 19.52% / 19.89%, RPCs 31.44%, framed bytes 34.12% /
34.14%, and observed Python fixture read-plus-write bytes 11.85% / 11.82%.
Current-step stdout falls 19.00% / 19.36%. Warm framed-byte ranges are file:
main 6,253,744–6,254,276, candidate 4,119,902–4,120,628; SQLite: main
6,264,262–6,264,682, candidate 4,125,580–4,125,850. No significance is inferred.
Observed warm request counts include `quota.turn_envelope.evaluate` 20 → 10,
`todo.context.page` 178 → 62, and capability-hook validation 8 → 4. These are
cumulative C1 differences, not savings attributable to the new read validator.

The primary metric remains the same producer's complete capture F → selected
BODY. All 60 pairs match the published producer capture digest, recovery and
selected-file digests, original identity and control facts:

| Provider / scenario | Main F → BODY B (reduction) | Candidate F → BODY B (reduction) |
| --- | ---: | ---: |
| file / cold | 59,828 → 27,845 (53.46%) | 53,649 → 27,120 (49.45%) |
| file / warm | 74,870 → 27,797 (62.87%) | 61,049 → 27,072 (55.66%) |
| file / recovery | 75,086 → 27,921 (62.81%) | 61,241 → 27,194 (55.60%) |
| sqlite / cold | 60,054 → 28,062 (53.27%) | 53,867 → 27,274 (49.37%) |
| sqlite / warm | 75,096 → 28,003 (62.71%) | 61,267 → 27,274 (55.49%) |
| sqlite / recovery | 75,316 → 28,131 (62.65%) | 61,459 → 27,403 (55.41%) |

BODY is smaller while its reduction ratio is lower, because the full source is
also smaller. Saved-file delta is not model retention. This new cohort supports
lower measured engineering overhead at the same synthetic artifact outcome;
it does not replace older cohorts or establish lower model-generated output,
cached/uncached input, fees, native-provider/SQLite-page/full-process IO, CPU,
latency, natural adoption or long-horizon benefit. The original budget failure,
historical-reader obligations and complete C1 acceptance remain open. No gate,
default, scoring or runtime behavior changes in this measurement update.

### Transitional journal intake

The canonical resume loader checked a complete source only for current v1
plans. Recorded v0 plans with a supplied `work_contract` instead took the raw
legacy identity branch, although the writer, ordinary inspection and settlement
lookup already rejected a changed source. The executing path still refused it
downstream; this is an inconsistent recovery intake, not evidence of an
unauthorized Host launch or debit. Explicit retry inspection likewise returned
its blocked observation instead of the v1 intake error.

The loader now sends every supplied complete source through the existing
`turn_host_lineage`/TypeScript source owner, including the recorded transitional
source/adapter cross-check. Current v1 loads already use that call; transitional
loads add it. No normal-path RPC saving is claimed. Source-absent historical
plans retain their exact raw identity checks and original bytes. The change
does not impose whole-journal settlement consistency on plan loading: existing
recovery inspection remains responsible for those diagnostics. Plain invalid-
journal inspection still returns its structured blocked observation.

The existing File/SQLite journal matrix now tests loader refusal for changed
content, unknown version, null/missing binding, conflicting adapter and signed
interaction/action/reference mutations. Numeric source values, current missing-
source refusal, bare historical loading and byte preservation remain controls.
The public `run-once --resume-turn-key` and explicit retry-inspection entries
exercise the same source refusal without rewriting history or spending quota.
The frozen pre-fix loader fails both transitional provider cases; after fixing
the CLI fixture's omitted required project argument, explicit retry inspection
also fails those two cases while both current-plan controls pass. Those are
distinct product and harness findings, not six product defects.

The pre-integration recovery cohort passes 139 cases in both source and an
isolated installed wheel. Integration with main `2852eb453bef` preserves the
complete native handoff, both lifecycle assertions and shared TypeScript imports.
Its 81-case source cohort and 178 TypeScript cases pass. An interrupted source/
wheel attempt has no terminal result and is not counted as a pass.

The integrated wheel initially passes 67 cases and fails 14 settlement cases:
their subprocesses default to the source checkout, which accidentally satisfies
workspace verification there but cannot do so in an off-checkout installation.
A diagnostic observer identifies the real workspace refusal. The existing tests
now explicitly use their own producing project; all original assertions, bad-
workspace cases and once-only recovery controls remain. The complete affected
module then passes 23 cases in both source and the unchanged installed wheel;
58 unaffected cases passed in the earlier wheel cohort. This covers 81 distinct
cases across those cohorts, not one uninterrupted final 81-case run. All 1,898
packaged source files match; the final 23-case wheel run observes 125 completed
processes and 64,109 Core module origins without checkout contamination. Sudden
exits remain outside that exit audit. Earlier failures remain recorded.

This repairs a supported recovery consumer rather than deleting a reader to
increase retirement counts. Legacy constructors remain only in the named
historical/composition/model-behavior fixtures above; original outstanding work,
supported recovery windows, independent review and complete C1 acceptance still
govern retirement and adoption. The preceding cost cohort remains pinned to its
original main/candidate snapshots; this integrity change does not remeasure it.

## Earlier integration evidence

The integration preserves main's extracted quota-presentation owner and sparse
native journal transport. One existing numeric collector and validator replace
the candidate's duplicates. Acceptance, summary lanes, authoring and Vision
sharing reuse the existing source-identity/edit owners; ordinary JSON equality
cannot establish original numeric identity across the Python/JavaScript boundary.
Body-size decisions still compare serialized bodies, not identity framing.

An independent 24-case Python-to-TypeScript regression first produced 16 failures
and eight passing controls. After the identity/edit repair, all 24 pass, including
integral floats, signed zero, unsafe integers, later exact authoring owners and
identical audit controls. The broader integration initially failed stale fixture
hash references and one absent-authoring CLI case; those failures remain evidence
and require separate corrected validation. This is source/transport evidence,
not installed adoption, accepted model outcomes or complete-cost qualification.

The 2026-11-01 heartbeat consumer increment (`18b55e04d`) reuses the existing
heartbeat rule owner for routing, safety and complete work guidance in captured
native Goal input. Fresh on/off prompts share that wording; historical saved
bodies are not rewritten. Clause review retains the actor, authority, required
reads, original/cumulative outcomes, ordering, evidence, continuation and stops.
This removes a separately maintained prose copy, not a decision or settlement
owner, and changes no activation default or protocol.

With the same CLI caller, 320-character scope, explicit CLI and five capability
flags, substituting only frozen `958eef4c` renderers gives 5,041/5,080 normalized
characters unbound/bound; the candidate gives 4,859/4,898 under the unchanged
4,900 allowance. Each candidate body saves 180 UTF-8 bytes. The longer-route
negatives remain over budget at 5,051/5,090. Captured native Goal input instead
grows 3,187→3,235 characters while staying below its unchanged 4,000 host limit.
These are generated-input measurements, not a full base-repository rerun,
control-packet compression, model adoption, token savings or outcome benefit.

At that checkpoint, the consumer checks pass 114 prompt/platform cases with one platform skip and
12 real isolated File/SQLite CLI final-input/recovery cases using synthetic host
executables. The output/differential
matrix still has five failing tests: complete quota Markdown 30,462>10,000,
crowded plans 24,044/27,508>22,500, complete alias 24,252>13,000 and its separate
multi-agent probe 25,933>13,000. Its other 219 cases pass, with one skip. No
budget or population is relaxed, and the historical restoration allowance is
not inherited by the new wording. C1 cutover, remaining readers and the original
quota single-session outcomes/full-cost comparison remain open.

The subsequent Markdown increment (`b852af5f7`, integrating target main
`035f7ac66`) removes nested pretty-print padding through the existing complete
Turn renderer used by both ordinary quota and the alias. Each root field has
its own line; every nested value, source clause and binding remains in one
parseable JSON block. JSON output, captures, native host input, legacy rendering
and decision/settlement owners are unchanged. The same-source comparison with
the frozen `f3ae16362` renderer includes the final stdout newline:

| Synthetic source | Previous Markdown bytes | Current Markdown bytes | Saved bytes | Complete JSON bytes |
| --- | ---: | ---: | ---: | ---: |
| Small | 30,462 | 24,531 | 5,931 | 24,252 |
| Crowded | 47,237 | 37,846 | 9,391 | 37,555 |
| Crowded with Vision | 55,893 | 45,249 | 10,644 | 44,954 |

Line counts fall from 860/1,214/1,361 to 69/72/73. Markdown still adds
279/291/295 bytes over the corresponding complete compact JSON; this removes
presentation padding, not semantic control-packet content. The complete-source
module passes 63 cases, including unknown facts, integral floats, signed zero,
unsafe integers and escaped instruction text. The integrated bounded-reader
buffer checks pass two cases. The output/differential matrix remains
219 passed, five failed and one skipped: small Markdown is now 24,531>10,000;
the other four failures retain their preceding sizes and limits. Historical
failures remain failures. No ceiling, fixture population or requirement changes,
and no model, invoice, installed-adoption or long-horizon benefit is qualified.

The optional Host increment (`8316c9fa5`, subsequently integrating target main
`5a9c0a612`) moves Ark and MemT tool identity to the existing complete dispatch
owner through `extensions.turn_host.extract_turn_work_identity`. Complete input
uses the selected-work owner, including adaptive primary work; historical input
retains the providers' raw declared values and strict string/NUL checks. Session
lineage keeps its historical normalization. Fresh-context, source/projection
integrity and MemT exact-Goal-lifetime refusal remain in force. This retires two
provider-side identity decoders, not the remaining DSH, native-child, steward and
session-recovery compatibility readers or the executor's v0 transport.

MemT candidate validation now retains the original request and complete source.
A real candidate conversion followed by source invalidation was previously
accepted by this local check; it now rejects. This does not establish that outer
Core settlement previously accepted that candidate. Actual CLI input still lacks
an admitted MemT lifetime and cannot open memory; converter tests do not invent
one. SDK tests use isolated synthetic protocol peers, not a GPU or model service.

A matched comparison calls the actual Ark identity consumer at frozen
`46bdaddb3` and the candidate on identical synthetic File/SQLite CLI requests:

| Cost observation | Before | Candidate |
| --- | ---: | ---: |
| Checked identity RPCs per request | 1 | 1 |
| Canonical JSON argument bytes, File / SQLite | 41,286 / 41,338 | unchanged |
| Canonical JSON result bytes | 211 | 324 |
| Complete-source deep copies for identity | 1 | 0 |

The avoided copy's source is 28,432/28,458 canonical JSON bytes; this is a source
size proxy, not allocated-memory measurement. Every dispatch-context response
now carries the three identity facts, adding 113 bytes even for its other callers.
Model prompts remain byte-identical at 33,020/33,046 bytes, verified by digest.
These counts exclude RPC framing and establish neither latency nor net model,
token, invoice or long-horizon benefit. Complete packet compression is unchanged.

Ark/MemT suites pass 41/43 cases after baseline 39/37 passes. The added tests first
failed on both providers' identity decoders and on MemT's invalidated-source
acceptance. Core capture/Host checks pass 58 cases, including File/SQLite actual
CLI input, adaptive identity, raw historical values and refusal without fallback.
After integrating main, the envelope/interaction/conversation owners pass 122
TypeScript cases. The native steward fixture initially has ten startup failures
because it inherits an ambient conversation-provider override that its fake peer
does not echo. Removing only that override in the test child process gives ten
passes; no user configuration or production provider check is changed. The first
failure is retained as test-environment evidence, not a passing execution.
An initial assertion incorrectly imposed complete-input adaptive selection on
historical provider decoding; its two failures were corrected and remain recorded.
The broader local matrices expose six context-delivery and two driver-profile
failures, all eight reproduced at frozen `46bdaddb3`; the five output-budget
failures also remain. They are not qualifying passes. C1 still requires these
delivery gaps, supported-reader retirement, packaged/installed qualification and
the original-quota outcome/full-cost comparison. Both optional packages require
the same integrated Core checkout; a version floor alone cannot prove seam
availability in a previously published wheel.

The bundled DSH follow-through (`e838e7594`) integrates target main `8ac384cdc` and replaces
its complete-input session/tool identity reads with the same checked selected-work
facts. Previously, adaptive complete work could select a different primary Todo
while DSH still bound tools and its reusable session to the old declared Todo.
The existing dispatch check now retains its already-returned identity privately
for that invocation; DSH reuses it for both bindings without another RPC. This
metadata is absent from final model input. The two historical DSH decoders are
consolidated into one raw-value adapter, preserving its false-like coercion,
component positions, Goal-instance hash and fresh-iteration hash. It remains
only for requests without `work_contract`, until their original-version recovery
window is reconciled; a supplied invalid complete contract never falls back.

At frozen `ae8ee460a` and the candidate, four identical synthetic requests from
real File/SQLite CLI plans exercise normal and adaptive work through the actual
DSH adapter. Each retains one dispatch RPC; canonical argument/result JSON sizes
remain identical. Final prompts are byte-identical by digest:

| Source | Normal prompt bytes | Adaptive prompt bytes | Added internal authority JSON bytes |
| --- | ---: | ---: | ---: |
| File | 32,377 | 32,517 | 114 / 115 |
| SQLite | 32,425 | 32,565 | 114 / 115 |

The final column is a Python-object JSON size proxy, not allocations or wire
traffic. Ordinary session/tool identities remain identical; adaptive bindings
intentionally change from the old Todo to the selected primary Todo. The probe
leaves state unchanged and spends nothing. It qualifies this caller correction,
not packet compression, latency, model billing or accepted-outcome/full-cost benefit.

The added regressions first produce four adaptive failures and seven passing
controls. The combined DSH and complete-source suites then pass 367 cases;
two additional real File/SQLite CLI cases pass through the built-in DSH runner,
independent validation, committed writeback and replay with one runner invocation
and one debit. Providers remain synthetic. The optional Ark/MemT suites pass
44/65 cases against the current Core. MemT initially lacked its pinned client;
after installing that client only in the isolated test overlay, five CLI cases
still imported an older installed Core from their task working directories.
The child import probe reproduced the missing seam, and explicitly binding the
test child's Python source path fixed those five cases without changing the
product or user configuration. Both initial failures remain evidence.
This uses the existing CLI/Chat Host owner and adds no capability, setting,
user step or activation default. No live SDK service or installed-host adoption
is qualified. Native-child, steward and
other historical consumers, the five output-budget holds, eight broader failures
and C1's original-quota acceptance remain open.

The native-child metadata follow-through (`3d58796d7`) integrates target main `07149df68`.
`configured_native_child_limit`, called by request construction, durable Host
attempts and both Codex transports, now reads the original complete interaction
context when `work_contract` is present. Only historical requests read the v0
context. The existing integer decoder, complete-source verification, launch
fences and native receipt admission are unchanged; this is a local transport
read, not a second authority or a new default. The shared source verifier already
rejects inconsistent projections, so this does not establish a prior wrong-child
admission. The remaining v0 envelope still supplies signed compatibility data.

New on/off regressions first fail on the historical field access. Real subprocess
and durable readback checks then preserve the enabled observations, disabled
isolation, parent result and private-content exclusion. Corrupt complete input
is refused before another process or receipt. The first wider run passes 195
cases but rejects both new fixtures at the complete Goal-source gate; delivering
the actual fixture file and full revision repairs the fixture, without changing
that product gate. The corrected native-child module passes 38 cases; the full
same three-module rerun passes 197 in 149.37 seconds. The owned
operation Host suite passes 34, with its two explicitly live-model qualification
cases skipped; no live host was launched.

At frozen integration `5d9dbb811` and the candidate, eight fixed requests combine
the earlier real File/SQLite normal/adaptive CLI fixtures with synthetic on/off
capability context recomposed by the existing owner. Metadata reads use zero
RPCs; complete input preparation still uses one. Request bytes (40,202–40,778),
prompt bytes (30,744–31,030), schema hashes and canonical argument/result sizes
are identical in every matched case. This is no packet reduction, latency,
allocation, invoice or full-Host cost measurement. The five output-budget holds,
eight broader failures, other consumer/writer migration and original-quota
outcome/full-cost acceptance remain open; the historical failures are retained.

The later source-checkout recheck at `d1046499a` runs both complete
`test_context_delivery_cli` and `test_loopx_turn_driver` modules: 253 pass and
only the two profile-alias cases fail. Their actual CLI response rejects capture
without a Turn identity before the test can inspect `scheduler_hint`; it is not
evidence of a scheduler-policy failure. The fixture now supplies an original
Turn and independently covers explicit delivery off and native delivery on for
both aliases. The on cases follow the declared original-capture reader through
contiguous UTF-8 pages, retaining all scheduler assertions. They verify one
admission, no spend and unchanged Goal/event-log bytes after recovery.

The same complete modules then pass 257 cases in 118.43 seconds, with no skips
or errors; the focused four-case matrix also passes. This increment changes
tests only and introduces no production setting, default, caller protocol or
runtime behavior. The six older context-delivery failures remain historical
negative evidence; their individual causes were not reassigned by this recheck.
The five output-budget holds, remaining actual consumer/writer migration,
supported-reader exit and original-quota outcome/full-cost qualification remain
open. A passing source-checkout cohort proves neither installed-host adoption
nor packet-size, model-retention or net-cost benefit.

The subsequent planning increment removes a redundant `consumer_version` RPC
only when `build_loopx_turn_plan` receives a complete contract. Its existing
`driver_lineage` operation already calls the same TypeScript version, integrity,
source/projection and selected-work owners. Historical inputs retain their
standalone version diagnostics. There is no new validation implementation,
cross-invocation cache, field, protocol version, activation default or authority.

Eight fixed inputs originate from real isolated File/SQLite CLI plans and cover
complete/historical sources with fresh/resume session policies. Against frozen
`0cd09b46c`, all final plans, Host requests, Codex prompts and schemas remain
byte-identical. Complete planning uses one RPC instead of two; the measured
plan/request/prompt/schema sequence uses two instead of three. Its canonical
RPC argument-plus-result JSON changes from 92,598 to 81,554 bytes for File and
92,838 to 81,752 for SQLite, saving 11,044/11,086 bytes (11.93%/11.94%). Historical
controls retain their three RPCs and exact bytes. Every complete model input
still contains the full original contract and fixture Goal; the probes spend
nothing and invoke no model. CSV, SQLite integer totals and Decimal ratios
independently agree on the eight matched comparisons.

The six-case planning matrix first has five duplicate-call failures and one
passing historical control; all six then pass. Unsupported adapter versions,
changed source content, a self-consistently rehashed but substituted adapter and
an explicitly null complete contract still refuse through the shared owner.
These are component transport costs, excluding framing, latency, allocations,
model usage and outcomes. Final model input is unchanged; the five output holds,
original-quota single-session comparison and complete C1 qualification remain
open. This saving cannot be added to the physical packet or selective-read
figures as though they shared a denominator.

The full Turn driver, native Codex CLI and complete-source modules pass 334
cases in 202.77 seconds, with no skips or errors. These include real File/SQLite
delivery, validation, failed-attempt recovery and original-Turn settlement; no
live Host was launched. This qualifies the bounded source increment, not the
remaining C1 cutover or a new exact-head merge-readiness receipt.

The subsequent Host-transfer repair preserves a supplied `work_contract` even
when it is not an object. Request construction and original-capture publication
previously discarded these values, allowing the native prompt and shared Host
reader to interpret them as historical absence. Both transports now retain the
value for the existing TypeScript complete-source validator; genuine historical
absence still uses the old reader. No parallel validator or new field is added.
The ordinary executor's earlier source check remains; this does not establish
an outer settlement bypass.

At frozen `42b842f93`, the corrected 18-case matrix has ten missing-refusal
failures and eight passing controls; the repair passes all 18. It covers both
transfers, absent/valid input, null, false, zero, list, text, empty object and
invalid integrity, including native/shared-reader refusal and no DSH runner load.
Two initial controls correctly refused an unfulfilled Goal read; delivering the
complete synthetic Goal fixes those fixtures, without relaxing that check.
Eight fixed File/SQLite fresh/resume inputs retain byte-identical plans, requests,
prompts and schemas, as well as unchanged RPC counts and argument/result bytes.
This is compatibility-boundary qualification, not a new compression or net-cost
result. The original five output holds and complete C1 acceptance remain open.

The wider six-module run initially passes 522 and fails six Host-context cases
before dispatch. Frozen `42b842f93` reproduces all six. An independent child
import probe finds a different installed Core when cwd is the disposable Goal.
The test now binds and checks this checkout for its subprocesses and explicitly
selects delivery off for its historical direct-body control. That complete
Host-context module passes eight cases; the other five modules retain their
520 passes on unchanged product files. The combined 528 distinct passing cases
come from those two invocations, not a claimed single final six-module run.
Original failures remain recorded; no installed Core or user setting is changed.

The managed Session's independent-validation reader now shares that same
complete-source owner. Preparation reuses the invocation-local identity already
checked by dispatch; after model work, the callback checks the current complete
source before running the original independent argv. Supplied invalid contracts
cannot enter the historical decoder. Actual absence retains the old selection
and diagnostics. Canonical declaration checks before and after argv, original
actor admission, leases and once-only settlement remain with their owners.

At frozen `bd2ea9c58`, four File/SQLite changed/null-source cases incorrectly run
the independent argv; four valid/historical controls pass across two baseline
invocations. The repaired eight-case matrix passes in one invocation. Faults are
injected at the actual post-model callback; the native Session, canonical task,
independent argv and settlement remain real, with only synthetic model/provider
fixtures. Six earlier startup failures came from an ambient provider override;
the shared synthetic fixture now isolates it without changing installed settings.
This establishes the callback boundary, not an outer settlement bypass.

A same-source native-fixture comparison substitutes the frozen baseline methods
without replacing their dependencies or validation command. Preparation adds no
Turn-envelope RPC; post-validation adds one, with 42,911/42,959 canonical argument
plus result JSON bytes for File/SQLite. A separate eight-input fixed CLI-source
comparison measures 36,320/36,404 bytes for complete inputs and no added RPC for
historical ones. These populations are separate. Both exclude framing, latency,
allocation, canonical task reads, argv, model usage and recovery; the extra
baseline argv is read-only fixture validation, not a production replay. This is
a measured component cost increase for source refusal, not compression or a
complete-net-benefit result. The original five output holds, remaining C1 readers
and original-quota single-session outcome/full-cost acceptance remain open.

The wider attempt at `9baa678db` completes five native/steward/calendar modules
with 359 passes, then stops after 37 identical provider-startup failures in the
sixth private-conversation module, which also has 56 passes. This is an interrupted
run, not a six-module pass; its nameless interrupt record is not a test. Two
same-source environment-unset controls pass, then the same two cases pass with
only the neutral ordinary fixture isolated. The production provider check and
installed configuration remain unchanged.

Target main `8a9e11628` is subsequently integrated. Its additional explicit
fixture caller is adapted at `4cdf977c3`; the runtime change remains `a49625e3a`.
On that integrated source, the complete native-validation/ordinary modules pass
68 cases in 335.058 seconds, and private-conversation/Antigravity modules pass
316 in 644.420 seconds. These are 384 distinct passing cases across two
invocations, not a new full six-module run. The shared conversation-binding
TypeScript suite passes 45 cases. There are no skips or errors in these final
checks. Earlier failures and component measurements retain their original
source; this integration does not remeasure full costs or qualify C1 cutover.

The blocks below preserve the candidate narrative moved out of the normative
RFC during this integration. Their measurements, proposals and present-tense
statements belong to their original checkpoints; they are not fresh claims for
this integrated source. The current RFC and named owners govern current behavior.
In particular, the retired scope prototype does not restore a mandatory model
reading plan. Existing negative results, original-quota comparison and reader
retirement requirements remain in force. The directory's other dated entries
retain their own provenance and are not rewritten by this relocation.

## Historical candidate block 1

The Agent-scoped implementation and measurements below remain an unmerged C1
integration checkpoint. They do not describe default installed behavior. The
shipped acceptance-material slice and candidate-stage proposal follow separately.

For cold delivery, use the current Agent's outcomes as the default expanded
scope. Other Agents' independent outcomes remain discoverable in an index with
identity, owner, objective, revision, work links, unmet requirement identities,
current review/check status and exact captured detail references. Global Goal
intent, shared requirements, permissions and stops always remain in the required
input. Expand cross-Agent dependencies, shared acceptance and material blockers
or conflicting evidence before the affected decision, regardless of ownership.
Goal-wide replanning and final acceptance widen the scope to every affected
outcome; a local delivery slice need not reread unrelated historical evidence.
Ownership is a default, not a permission boundary or sufficient relevance test.
Unbound work, unknown relations or unsupported source shapes retain complete
delivery until the existing typed owner can prove the narrower requirement set.

The canonical contract still retains complete requirements and evidence. Reuse
the existing exact-capture outcome indexing and staged replan reader as the
starting point for one ordinary/replan/detail pipeline, extending its index with
current decision facts rather than merely outcome counts. Preserve full text
for the required scope; defer supporting provenance and review detail through
integrity-bound reads with explicit ordering. A dependent decision must consume
its required detail first. Keep current conclusions, freshness and unresolved
requirements inline; an Agent review must not become an independent check.
Interface folding alone does not reduce model input. The delivery projection,
required-read owner and host recovery contract must migrate together; existing
captures retain their original complete-read obligations. Missing detail or
uncertain retention selects the original capture's supported recovery, never a
new admission or an assumed read receipt. Do not shorten retained requirements,
prune declaration origins or infer relevance from prose to meet a byte target.

Qualify this C2 change on the same cold, retained warm and context-loss sources:
default own work, cross-Agent dependency/constraint, unbound work, changed peer
evidence, broad replan and Goal acceptance. Count actual model input, paging and
detail round trips, recovery, latency and complete outcome cost. A smaller first
page is not a benefit when required follow-up reads or missed constraints erase
the saving. This is proposed delivery behavior; the current complete-source
reader remains authoritative until replacement and rollback are qualified.

Current integration evidence: [navigation convergence checkpoint](2026-10-29-navigation-convergence.md).
The [journal source-fidelity checkpoint](2026-10-29-journal-source-fidelity.md)
separates the independently reviewable persistence repair from the still-open
numeric hash/context-codec qualification. The subsequent
[context source-fidelity checkpoint](2026-10-29-context-source-fidelity.md)
splits generic cold/delta/full-recovery repair into normal #7661 and reuses one
source-identity/edit owner in C1. Its component transport reduction does not
qualify canonical hashes, installed adoption or original-quota full-cost benefit.
Existing hash semantics remain unchanged.

The current C1 branch reuses merged #7560's
`outcome_context_navigation_v1` and the generic same-capture `context-read --select`
reader. Registered relations guide reading order; ownership does not prove
independence, and limitations do not create a full-file model-reading gate.
Complete original requirements, current states, global constraints and contrary
evidence remain reachable in the original digest-bound capture.

The earlier unmerged mandatory reading plan, `outcome_context_projection_v0`
wrapper, outcome-specific ordinary reader and fused native projection operation
are retired. Staged replan's existing decision-bound acceptance reference stays
with its owner. Native IO uses main's material planner and exact recovery codec;
it commits the current complete recovery before optional planning. This restores
two owner calls for a simple native capture instead of the prototype's fused
call, so the old call-count improvement is not a current qualification.

Merged [#7627](https://github.com/loopx-project/loopx/pull/7627) supplies
saved-file-first delivery and file deltas without mandatory full model reading.
C1 adopts its reviewed implementation through the existing native owner,
without reintroducing the retired scope mechanism. Its installed adoption, actual
model decisions and accepted-outcome/full-cost qualification remain separate.
Historical captures retain their original identity and supported recovery.

The following measurements and tests describe the retired prototype's exact
checkpoints. They remain historical evidence, not results for the current
navigation/planner integration.

On one 24-outcome / 253-requirement synthetic snapshot, 61 alternating warm
real-provider owner calls against the prior implementation measured File median
5.890→6.317 ms and SQLite 2.239→2.521 ms; p95 was 15.267→17.580 and
6.504→9.412 ms respectively. All prior response fields were identical; plan
metadata added 405/415 serialized bytes. This isolates the acceptance owner in
one TypeScript process, excluding bridge, CLI, cold startup and model costs.
That foundation added cost before the native scope cutover; it was not an
efficiency win. At the scope-cutover checkpoint, native presentation additionally
allocates an exact required artifact and calls the same typed owner once before the existing codec. These
costs must be included with page, detail and recovery costs; a smaller first
input does not establish a net benefit.

The native slice passes 406 real-entry Python tests (five explicit native/SDK
cases unqualified), 169 TypeScript tests and four actual Codex
`0.163.0-alpha.11.1` deterministic-loopback checks. The latter inspect actual
model-request bytes for cold/full, retained delta and same-capture recovery;
they do not establish free-form model behavior. Four new File/SQLite scope
assertions fail at `1ac29089f`. An integration recovery-reference failure was
repaired by replacing metadata only for explicitly scoped artifacts, preserving
ordinary original-file byte identity.
An interruption regression first failed with the preceding capture still
selected; publishing current recovery before the optional scope call fixes it.
At runtime `e1c69289a`, standard premerge failed two checks also reproduced on `8b37000b3`:
the existing oversized `run_loopx_turn_once` decision function and stale
`turn_route` producer coverage after the earlier owner convergence. The native
increment did not change those files; that historical result is not a pass or
an exception. The integration was rebased onto `b69021498`, then onto
`f63647e2e` with the target-main native output-budget behavior retained. It incorporates
the actual TypeScript producer/scanner repair from [#7520](https://github.com/loopx-project/loopx/pull/7520).
It also isolates original-capture restoration from run-once orchestration at
`fec9cd3af`, preserving the existing capture owner and session recovery. The
size thresholds remain unchanged. Current combined qualification belongs to
the exact integration head's PR evidence, not the old native-slice test counts.

Final measurements include the recovery-publication fix at runtime commit
`e1c69289a`. Against immutable `8b37000b3` (the preceding C1 plus the same target-main large
page profile), three warmed cohorts per side supply 21 samples per provider and
phase on a 24-outcome / 253-requirement source. The warm case adds a real typed
dependency and verifies delivery of its previously unread body. Complete
serialized page output for File cold/warm/recovery is
105,380/40,473/103,372→37,908/33,495/38,594 bytes. Median latency is:

| Phase | File, before→after ms | SQLite, before→after ms |
| --- | ---: | ---: |
| Cold full input | 72.856→48.749 | 67.237→46.477 |
| Warm scope expansion | 38.744→51.944 | 35.953→52.165 |
| Lost-context full recovery | 109.126→61.425 | 109.725→56.777 |
| Index plus one optional complete detail | 96.918→108.557 | 92.460→109.251 |

In that measured version, the additional scope RPC remains: warm bridge calls rise from two to three.
File warm artifact files total 131,020→160,192 serialized bytes; the index/detail
operation still uses two calls and now revalidates the derived presentation.
Cold and recovery use one page instead of two. These measurements cover Python
native projection, pages and lifecycle with warmed runtime; machine load was
not controlled. They exclude initial acceptance collection, process startup,
child CPU, model/cache tokens, billing, human/setup work and accepted outcomes.
Cold/recovery transport improves while warm and detail medians regress. This
is not the original-quota comparison or a positive complete-cost verdict.

At `85c235d29`, the native caller combines scope and encoding through the same
owners in one request and removes the unused full-wire copy. The former standalone
scope operation is retired; ordinary codecs and captured-detail readers remain.
Against `307d000a3`, the same frozen File/SQLite observations and three interleaved
warm cohorts per arm (21 samples per provider/phase) measure:

| Phase | File median/p95, before→after ms | SQLite median/p95, before→after ms |
| --- | --- | --- |
| Cold full input | 37.438/60.718→28.516/38.941 | 34.263/39.924→26.966/39.054 |
| Warm scope expansion | 42.065/58.640→34.813/45.922 | 39.869/49.047→33.856/45.593 |

Including page-lifecycle observation, these phases now use two rather than three
bridge calls. File cold artifact bytes fall 162,877→129,299; warm artifacts remain
160,192. Complete page output is unchanged. Six same-path ordinary/scoped/broad
File/SQLite comparisons preserve complete public pages and recovery values;
the one-call regression assertion fails on the baseline. Warm response payload
grows by 27 bytes despite lower request traffic. Detail/recovery keep their call
counts; SQLite detail p95 worsens 138.131→175.541 ms without a changed code path.
Machine load remains uncontrolled and the earlier exclusions still apply.
This removes demonstrated local transport/IO cost, not the original-quota
outcome/full-cost gap, short-source growth or all warm artifact overhead.

Small sources provide a counterexample to size savings: four real-entry
File/SQLite fixtures with two outcomes and four requirements grow from
36,293–40,548 to 37,948–42,203 bytes, exactly 1,655 bytes each. Index and scope
metadata exceed their short optional bodies. The proposal does not derive a
semantic omission rule or production size threshold from the larger fixture.

On the same 24-outcome / 253-requirement synthetic source, rendering the former
count-only index shape allocates 8,190 bytes; the staged state groups allocate
18,979 bytes. The old 12 KiB index budget fails. Retaining these decision facts
justifies a separate 20 KiB index ceiling; history, horizon, whole-envelope and
production performance limits stay unchanged. Grouping exact states avoids
repeating the same status fields for each requirement. These are serialized
bytes, not a token/latency/outcome benefit or an immutable base/head comparison.
The source-path scope checks and transport costs above do not qualify general
semantic independence or matched accepted-outcome/full-cost benefit.

## Historical candidate block 2

The external scheduler candidate now validates a versioned complete input through
`assert_complete_turn_decision` and the existing TypeScript complete-reader owner
before reading cadence or wake permission. This is a pure reader: it neither
recomposes the captured host binding nor creates a legacy executor view. Missing
bindings, unsupported versions/required semantics, altered content and invalid
same-capture references fail before the wake command. Unversioned ordinary quota
packets retain their existing compatibility path; retiring that path requires
reconciling supported deployed workers. File/SQLite source-CLI-to-worker tests use
local child markers, not model execution. This qualifies that input boundary,
not attached-host task delivery, settlement or accepted-outcome/full-cost benefit.

The optional Ark adapter reuses `extract_turn_authority` / `render_prompt` from
that same host-input owner; no optional-package contract builder is needed.
File/SQLite `turn plan --host generic-cli --iteration-context fresh` now feeds
its complete request through the real Ark SDK with a synthetic HTTP transport.
Checks compare the actual user-message contract with the original CLI source,
recover a persisted running receipt without a new input, replay completion
without provider calls, and reject altered source/version/binding or explicitly null
complete input before provider or receipt creation. Core state and spend remain
unchanged. Historical Ark request and recovery tests remain. This qualifies the
SDK wire boundary, not a deployed cloud session, model outcomes or Core settlement.
Ark and MemT now share the real File/SQLite CLI request fixture. MemT rejects
that complete input before provider IO because the executable legacy registry
supplies no exact `goal_ref`. The existing `source_session_v1` profile carries
an exact lifetime but is lifecycle-only: the real generic Turn planning entrypoint
rejects it before capture or execution. Supplying a synthetic reference after
planning would hide this producer/consumer gap. Complete-input corruption also
rejects before provider or receipt creation; historical MemT SDK/snapshot tests
remain, but they do not qualify end-to-end complete-input delivery. The owning
change on the MemT path must carry an admitted exact lifetime from an executable registry
through the shared Turn request and recovery owners, preserving recreation and
stale-instance rejection. Do not relax MemT's isolation or enable the
lifecycle-only profile as an adapter workaround. This is a demonstrated adoption
gap, not a qualified native-memory path. The operation-SDK native runtime and
packaged App adoption also remain unqualified. The internal effect envelope and historical request decoder
still serve settlement/result/session callers; remove them only after those
callers and outstanding receipts use the replacement, not merely because model
input no longer contains the old envelope.

New Codex/DSH requests now read their shared dispatch session lineage from the
complete contract, reusing the TS complete-input validator and adaptive primary
work owner. Model-input validation uses the same owner and rejects mismatched
Goal/Agent/Todo identities between complete input and the internal effect adapter,
even when both digests are independently valid. The complete path no longer
repeats Python version/signature validation; historical requests keep their
original decoder, persisted session schema, path and scope. Real File/SQLite CLI
capture, failed-attempt recovery and once-only settlement replay pass. Ordinary
and adaptive work restore existing Codex/DSH sessions under Todo and Agent
scopes. The same fixture preserves legal baseline paths but fails the added
identity-conflict cases before migration and passes on the candidate. Native
operation SDK environment tests still include skips; its runtime is not qualified.
Session identity migration does not retire all result qualification and settlement
readers of the internal envelope. Full retirement and net-cost
benefit remain unproven. The MemT lifetime gap blocks its dependent path only,
not these independent migrations.

Planning and failed-attempt session lookup now pass the original complete contract
through the same identity owner, including journal inspection. An omitted contract
still decodes historical records; an explicitly null contract rejects. Live and
cached vision-result normalization also use this shared identity read instead of
rebuilding Goal/Agent from the effect adapter. Session scope/lifetime fences and
the native app-server callback remain with their existing owners. This removes
those identity-only reads, not the full internal adapter: execution routing,
result evidence/profile interpretation and settlement still require migration.
Adaptive recovery fixtures are composed before signing: historical unverified
selections preserve primary-Todo writeback, while a conflicting verified task
source rejects before host execution or quota spend. The source fence is retained.
Completion-recovery qualification checks the original durable completion and spend
receipts without resubmitting completion. Four obsolete replay-call assertions
also failed on the prior baseline; their repaired oracle retains changed-task-text
and historical pre-source acceptance cases on real File/SQLite stores.

Completion task-body resolution now lives with the existing TypeScript interaction
source owner. The same coarse read now supplies `run-once` with the original
selected work, task-body commitment, admitted action, replan and writeback
conditions. Complete-source validation reuses its already-checked effect
projection within that call; it adds neither another RPC nor a model-facing
contract. Adaptive primary selection and ordinary selection share the existing
interaction owner. The CLI no longer reads these execution facts from the
historical adapter; it still transports that adapter for source/signature checks
and consumers not yet migrated. Omitted complete contracts retain the old journal
decoder; supplied null or invalid sources never fall back. Exact Unicode and
whitespace bytes, missing/ambiguous-source refusals, original settlement identity
and accepted-receipt recovery remain protected. The old 48-case File/SQLite
capture/completion selection passes at
`599cf1f72`; candidate complete-source checks also reject altered bindings and
explicit null inputs. The initial task-hash migration added a TypeScript boundary
where the historical resolver was local Python; this broader read reuses that
boundary rather than canceling its cost. Full adapter retirement, remaining
effect/material consumers and original-quota outcome/full-cost qualification
remain open.

Structured Host preparation now consumes the checked complete source directly;
it no longer flattens the old action, boundary, capsule and planning carriers for
new requests. The same typed source check supplies the executable-action alias
and admitted-replan fact, preserving capability-before-replan ordering without
reimplementing it in Python. Codex prompt construction also uses the complete
source for boundary guidance and the original model input's staged readers.
Original capture metadata still travels through the existing transport; its
source and adapter integrity checks remain. Historical-only requests retain the
old decoder and renderer. This is an internal consumer replacement, not another
model-facing view or a cache across attempts; the final complete model input and
its instruction obligations must remain unchanged. Other effect/session
consumers, packaged adoption and full-cost qualification remain open.

Against rebased predecessor `4c4e9b346`, frozen real File/SQLite CLI requests
produce identical complete prompts, result contracts, historical-only outputs
and invalid-source diagnostics. Host preparation still makes two RPCs; total
request bytes decrease by five and responses grow by 190 bytes per group. Six
sequential cohorts, with separate verified runtime namespaces and 93 warm
samples per arm/case, observe these median/p95 values in milliseconds:

| Request provenance / Host | Predecessor | Complete-source consumer |
| --- | --- | --- |
| File / Codex | 27.544 / 32.366 | 31.258 / 40.408 |
| File / structured | 25.948 / 32.642 | 30.007 / 39.242 |
| SQLite / Codex | 25.695 / 31.070 | 31.889 / 37.696 |
| SQLite / structured | 24.409 / 30.258 | 33.242 / 42.287 |

These observations are negative local costs, not a speedup. Provider labels
identify request provenance; the measured groups perform no authority-store IO.
Machine/background load remains uncontrolled. Startup, other calls, complete
Turn/model/cache/billing/human costs and accepted outcomes are excluded. Earlier
overlapping, non-isolated exploratory timing is not qualified; neither source
convergence nor these samples establish original-quota net benefit.

The next input-preparation increment shares one complete-source check between
prompt and result-schema construction within a single Codex launch. Both the
CLI process and operation-equipped app-server use this existing adapter boundary.
Standalone prompt/schema calls still check independently; structured result
conversion checks its source again after execution. No checked value is cached
across attempts, model work or result handling. The typed owner now returns only
action/replan/approval/profile applicability facts instead of echoing the full
source over the bridge. Python consumes its original checked input, retaining
the structured authority's detached values. Complete model input, original
readers, launch guards, result validation and outer settlement remain unchanged.

An owned V8 capture of 500 baseline source checks locates substantial CPU work
in canonical hashing and Unicode key ordering. It excludes Python, transport and
provider IO; its inclusive weights are not elapsed-time savings. This motivates
removing duplicate checks at one preparation boundary, not weakening integrity
checks or sharing validation across execution. Earlier negative measurements
remain historical evidence; this increment does not close C1's original-quota
outcome/full-cost or adoption requirements.

Against rebased predecessor `75bd69bf6` on target `ff9e97f8e`, six sequential interleaved cohorts
(93 warm samples per arm/case) retain identical complete prompt/schema/result,
historical output and invalid-source diagnostics. Codex preparation drops from
two RPCs to one; structured prompt plus post-execution candidate conversion
retains two. Codex request bytes are 86,293/86,399 → 43,144/43,197 for File/SQLite;
responses are 62,554/62,606 → 211. Structured requests decrease by five bytes and
responses become 422 bytes. These are internal JSON payloads, excluding framing.

| Request provenance / Host | `75bd69bf6` median / p95 ms | Prepared input median / p95 ms |
| --- | --- | --- |
| File / Codex | 22.147 / 26.204 | 12.003 / 13.653 |
| File / structured | 22.269 / 26.310 | 23.124 / 27.843 |
| SQLite / Codex | 21.656 / 26.511 | 11.734 / 17.183 |
| SQLite / structured | 21.324 / 23.608 | 23.716 / 26.079 |

Codex's local preparation improves; both structured timings remain negative
observations despite lower transport volume. The workload performs no store IO
or real model work. Separate owned runtimes and sequential arms avoid this
Agent's validation overlap, but background load remains uncontrolled. Startup,
other calls, full Turn, tokens/cache, billing, implementation/human cost and
accepted outcomes remain outside this measurement; it is not the original-quota
comparison or proof of overall benefit.

The Codex result schema and shared candidate adapter now use one checked
complete-contract profile read for evidence applicability. They no longer decide
that requirement from the internal capsule on new requests. The existing
TypeScript integrity/source/lineage validator serves both this read and model
input validation; no new model-facing packet view or per-evidence-field call is
introduced. Omitted contracts retain historical capsule decoding; explicit null
or altered contracts reject. Empty/non-object profiles preserve their previous
non-applicability, stop results still need no evidence, and supplied evidence is
passed unchanged to the existing result validator. This migrates applicability,
not evidence-content validation, execution routing or all settlement consumers.
Each schema/material-candidate read adds one checked boundary call; this is a
semantic convergence step, not an established performance improvement.
In a 31-pair warm-boundary measurement using one real SQLite CLI host request
(45,405 JSON bytes, synthetic provider), schema construction plus material-result
conversion rose from a median 0.013 ms at `68223e337` to 19.6 ms in the candidate
(p95 0.019/22.8 ms); legitimate outputs matched. This local added cost is retained
as negative evidence. It excludes cold startup, model work and the full Turn;
overall outcome/cost qualification remains open. Checks cover real File/SQLite
capture/recovery plus Codex/shared-host consumers; they do not prove packaged App
adoption or a model-outcome improvement.

The execution entry now checks complete-contract identity once before dispatch
or journal recovery. Settlement-plan fallback uses that checked identity, and
the recovery assessment reuses it within the same invocation instead of reading
the adapter's Agent again. Existing settlement plans and receipts are never
rebuilt, and omitted historical contracts retain their exact decoder. The value
is not cached across host attempts, source replacement or later invocations.
This also closes a recovery bypass: an existing settlement plan previously
skipped source validation, allowing a null/altered complete request to return an
existing journal outcome. On real File/SQLite CLI journals the new refusals leave
journal bytes and spend counts unchanged and invoke no host/settlement callbacks.
Ten targeted cases fail the new refusal assertions at `ce996ea0f`; the candidate's
32 capture/recovery cases and broader 605-case regression pass (overlapping).
The entry adds one checked boundary call, reused for both consumers. On the same
45,405-byte real SQLite CLI request, 31 alternating warm samples of the existing
settlement-plan read plus recovery identity preserve legitimate output parity:
median 0.016 to 12.382 ms, p95 0.024 to 14.883 ms. This is added local validation
cost, not an efficiency win; it excludes cold startup, full Turn and model costs.
Full-path performance and remaining routing/settlement retirement stay open.

Codex CLI prompt construction now delegates valid complete requests directly to
that existing complete validator, removing its redundant standalone envelope
version call. Historical requests retain their version check; a failed complete
validation still checks the version to preserve diagnostic precedence when both
inputs are malformed. No validation cache, new wire field or second owner is
introduced, and prompt text is unchanged. This is a call-boundary consolidation,
not retirement of the remaining driver routing or settlement adapter.

Against `e1dcd95dd`, complete and historical prompt bytes match, and six negative
input cases retain the same refusal diagnostics. On one 45,429-byte real SQLite
CLI request producing a 34,043-byte prompt, 61 alternating warm samples reduce
median prompt construction from 24.229 to 14.819 ms and p95 from 30.189 to
17.854 ms; bridge tracing confirms two calls become one. This local reduction
is about 39%, excluding cold startup, model execution and full-Turn cost. It does
not cancel the separately measured profile/settlement overhead or qualify the
original-quota outcome comparison. An earlier pre-diagnostic-refinement sample
was 21.279/12.994 ms median and 29.729/17.321 ms p95; retain it as earlier evidence,
not a second independent final qualification.

Planning now receives the complete contract before route/session/transaction
construction, rather than attaching it after the plan is built. Planning and
failed-session reconciliation share the existing TypeScript complete-source
validator through one coarse lineage operation. It selects Goal/Agent/work
identity and returns the action-signature preimage in the same boundary call;
it replaces the previous signature-only call, without a per-field RPC or cache.
Explicit null, unsupported or altered complete contracts reject, including an
already-resumed request. Omitted historical contracts keep their exact decoder.
The receipt owner still supplies Todo-less replan identity. The route and launch
increments below describe the subsequent consumer migration.

The Python JSON/hash transport codec remains deliberately unchanged. A direct
JavaScript hash trial failed numeric/exponent preimage parity and would have
changed persisted Turn keys; File/SQLite characterization now protects those
values, Unicode, adaptive work selection and resume idempotency. On one real
SQLite CLI request (45,380 JSON bytes), 31 alternating warm samples against
`088867421` preserve complete/historical plan and failed-session output parity.
Planning median/p95 changes from 39.632/47.964 to 42.608/53.270 ms; recovery changes
from 12.330/20.858 to 18.136/23.242 ms. These are local added validation costs,
not a full-Turn or original-quota outcome comparison. No model, installed App
or PostgreSQL authority qualification is implied. Remaining route/material
adapter retirement and end-to-end cost qualification keep C1 open.

The live Host launch recheck now composes the receipt-bound live observation
through the same complete-contract owner as initial planning, then supplies that
contract to the shared planner. This replaces its raw-quota-to-historical-plan
shortcut. Stop and changed-action refusals still precede composition; the live
observation neither replaces the original captured model input nor mints another
receipt. Remaining effect/material adapter retirement is still migration work.

The route classifier now lives in the existing TypeScript envelope owner. The
explicit registered action/frontier table is exhaustive at compile time; unknown
historical actions still reach the Host. Signature refusal, attempt, replan before
ordinary-delivery gating, capability intent identity, repair, user action and
quiet wait keep their ordering. Python retains a thin controller transport and
historical scalar-text encoding, not a second classification table. Complete
validation ignores historical decoding hints. The persisted Python action-hash
codec and historical selected-work reader are unchanged.

Planning receives the route in its existing coarse lineage/signature call;
complete and historical ordinary plans still use two bridge calls. Against the
previous Python classifier, 1,152 action/gate combinations agree; complete and
historical plan outputs also agree on a fixed real SQLite CLI fixture. These
planner comparisons use the same current TypeScript endpoint and therefore are
not an exact old/head end-to-end latency experiment. The standalone controller
route now needs one bridge call: 61 alternating warm samples on a 44,891-byte
request increase median/p95 from 0.004792/0.010125 to 13.252792/15.855042 ms.
That is a measured regression for this caller, not an efficiency gain. It remains
an explicit cost gap for controller boundary consolidation; no cache or duplicate
classifier is introduced to hide it. File/SQLite entry, recovery and once-only
settlement qualification, remaining adapter retirement, packaged adoption and
original-quota outcome/full-cost qualification remain separate acceptance work.

The production `turn managed-step` caller now combines envelope construction and
that same route evaluation in one TypeScript request. The Python adapter returns
a local immutable envelope snapshot paired with its route; this is neither a new
agent wire layout nor a second decision owner. Decoding or editing a returned
dictionary cannot change that snapshot or attach its qualification to another
wire dictionary. The controller retains its existing user-action precedence,
receipt/predecessor checks and journal-owned retry budget. Plain historical or
external envelopes still use the existing route reader and signature refusal.
The construction adapter shares host-binding and capture-argument preparation
with the ordinary envelope builder; the managed caller's redundant intermediate
builder has been removed. The standalone-reader regression above remains a
historical measurement; only the fresh production construction/consumption pair
can avoid that extra request. Remaining selected-work/effect/material decoding,
large cold-context recovery and packaged/outcome qualification remain open.
The local snapshot retires when this caller consumes the unified typed
controller result directly; it adds no persisted schema or compatibility wire.

On one fixed 31,088-byte synthetic source, 61 alternating warm samples compare
the previous Python producer/controller functions at `ddf96b79c` with the fused
call, using the same current TypeScript server and unchanged build/route rules.
The final-source component median/p95 is 22.477/25.417 versus 13.739/17.479 ms. A separate
public `managed-step` CLI comparison uses real isolated File/SQLite stores and
21 alternating warm samples per arm/provider: median is 135.989 versus 137.316 ms
for File and 149.310 versus 146.081 ms for SQLite; p95 is 209.730 versus 192.544 ms
and 222.299 versus 240.848 ms respectively. A separate transport-count probe
observes 70 versus 69 total bridge requests on each provider; this removes one
route request, not the other live-decision calls. Complete outputs agree, the failed
journal remains byte-identical and neither arm spends quota. Request count
improves; mixed whole-CLI medians and tails do not establish a speedup. Small local samples and
shared host resources limit tail inference. These are source-bound observations,
not an entire immutable old-tree comparison, cold-start, installed App, model
outcome or original-quota full-cost qualification.

Complete readers now also compare the adapter's signed action document with the
existing producer projection of the admitted complete source. Independent source
and adapter hashes previously proved only their separate integrity: a rehashed
adapter could retain Goal/Agent/Todo while changing execution or writeback facts.
The shared check covers the producer's existing signature dimensions without a
second field list, new RPC or wire view. Historical-only inputs keep their old
reader. Existing complete requests whose profile adapter disagrees with their
source now reject instead of silently reading the source profile; this deliberate
refusal change preserves the historical profile behavior separately.

Against `b9e2c5120`, File/SQLite planning refusal assertions fail before the fix.
Six independently rehashed variants cover run eligibility, effective action,
delivery permission, user intervention, execution policy and spend permission;
the candidate rejects them before host/writeback/spend callbacks and leaves no
Turn journal or settlement receipt. An existing lane lock can still be allocated.
On a 44,891-byte real SQLite CLI fixture, 101 alternating warm in-process
TypeScript samples preserve legitimate lineage output; median validation changes
from 4.864 to 5.163 ms and p95 from 5.570 to 6.010 ms. These are added local costs,
excluding bridge transport, live composition, cold startup, models and full Turn
cost. They do not qualify the original-quota outcome comparison or C1 cutover.

The candidate's ordinary planner and failed-session reconciliation now also
consume the selected work from the existing `driver_lineage` call. The shared
`selectedWorkTodo` owner places ordinary or adaptive selection into that call's
existing action-hash preimage; Python no longer selects it again or rewrites the
preimage. Python retains the persisted JSON/number and primary-ID scalar/whitespace codec.
The shared owner consumes already-decoded historical text without applying JS
trim again. Complete inputs
ignore those hints and retain full source/projection integrity checks. There is
no new bridge call, model-facing field, persisted schema or cross-attempt cache.
The public action-signature operation still describes the original envelope.

This replaces selection in the actual plan and failed-session recovery callers,
including the native CLI's original-capture retry. The remaining v0 Host,
settlement and controller readers still use their historical selection decoder;
they retire only after their callers and outstanding receipts migrate and pass
equivalent recovery qualification. This increment does not qualify complete
adapter retirement, App/SDK/benchmark adoption or original-quota net benefit.

Qualification against immutable `334c1a772` retains complete plan/recovery outputs
on frozen File/SQLite CLI inputs. Planning still uses two bridge calls and
reconciliation one, with equal request/response bytes on these ordinary inputs.
The actual CLI/provider/Host, completion and settlement modules pass 254 cases;
ten migration assertions fail on that predecessor while twelve historical-codec
cases pass there. The latter cover omitted/false/zero, whitespace, numeric,
boolean and collection-valued historical primary IDs, Unicode whitespace/BOM, version/mode applicability,
original Turn keys and resumed bindings. The candidate passes all of them.

A temporary `ef479d2b5` trial changed three Unicode whitespace/BOM identities;
those semantic counterexamples fail there and pass on the corrected owner,
while the original predecessor preserves them. Its measurements below remain
historical trial evidence, not qualification of the corrected source.

There is no qualified latency improvement. Three balanced sequential cohorts
give 93 warm samples per arm/case. The initial source-worktree versus archived
baseline comparison has higher candidate medians in all eight ordinary and
historical plan/recovery rows. Its generated-cache inventories differ. A second
control uses clean sibling source copies, identical inventories and disabled
bytecode generation in both arms; it still has mixed or slower observations:

| Complete-source input / operation | Initial baseline → candidate median / p95, ms | Clean control baseline → candidate median / p95, ms |
| --- | --- | --- |
| File / plan | 35.129 / 40.776 → 46.227 / 57.355 | 24.174 / 30.183 → 25.330 / 30.443 |
| File / failed-session recovery | 14.941 / 17.957 → 19.440 / 24.477 | 11.259 / 13.196 → 12.582 / 15.495 |
| SQLite / plan | 36.979 / 46.065 → 46.461 / 54.795 | 23.820 / 30.178 → 26.408 / 30.523 |
| SQLite / failed-session recovery | 17.404 / 22.606 → 18.759 / 22.139 | 12.586 / 16.141 → 11.503 / 14.124 |

The initial negative results are retained; the control is not a historical pass
or proof of their cause. These are raw-helper observations without store IO,
not full native CLI/Turn timings, cold startup, model cost or accepted outcomes;
background host load is uncontrolled. Separate owned V8 captures identify
existing Unicode key ordering/hash work, without proving the latency cause.
Python wait/IO stacks remain unprofiled because the optional profiler is absent.
Shared ownership and byte parity do not cancel these cost gaps or earlier
negative C1 measurements.

The corrected three-file source has a separate clean-source comparison, again
93 warm samples per arm/case with all eight complete outputs equal and unchanged
RPC/ordinary-input traffic. It retains mixed results, including a worse SQLite
planning tail. The same exclusions and uncontrolled-load limits apply:

| Input / operation | Baseline median / p95, ms | Corrected source median / p95, ms |
| --- | --- | --- |
| File / complete plan | 38.529 / 48.381 | 38.504 / 47.134 |
| File / complete recovery | 17.821 / 22.029 | 18.244 / 21.857 |
| File / historical plan | 32.787 / 44.413 | 30.871 / 36.733 |
| File / historical recovery | 10.635 / 14.620 | 10.292 / 12.201 |
| SQLite / complete plan | 39.600 / 46.547 | 40.036 / 77.714 |
| SQLite / complete recovery | 18.034 / 28.685 | 18.552 / 25.223 |
| SQLite / historical plan | 32.061 / 42.789 | 32.447 / 38.962 |
| SQLite / historical recovery | 10.444 / 14.258 | 10.270 / 12.891 |


#### Native execution and completion source

The native launch guard now consumes the selected Todo already returned by the
shared planner, rather than running the historical Python selector again.
The existing execution-context/task-commitment reader lives with the Turn
driver and serves both CLI preparation and completion-intent classification;
its CLI copy is removed. The semantic owner remains the existing TypeScript
`execution_context` operation, not a new view, schema or Python decision owner.

After Host execution and independent task validation, a complete request checks
that source again before choosing terminal versus ordinary completion. A saved
writeback or terminal outcome does not bypass this check. Malformed complete
input and adapter facts inconsistent with its source reject before writeback or spend;
the original journal can then recover the already validated result without
another Host invocation and settle once. Omitted historical inputs retain their
exact decoder and receipt recovery. Completion-outcome validation and the
typed settlement/effect owner are unchanged; this does not retire every
historical completion or controller reader.

The launch guard adds no bridge call. Complete validated-completion
classification adds one coarse source check at this later boundary, including
unfinished journal recovery; historical classification is unchanged. That is
an explicit additional cost, not a speedup or permission to cache a pre-execution
check across execution, attempts or recovery. Original-quota decisions,
accepted outcomes, complete costs and remaining consumer adoption stay open.

The four affected capture/driver/completion/settlement modules pass 264 cases
with real isolated File/SQLite CLI consumers and synthetic Host/validator
processes; the two typed-owner modules pass 79 cases. Twenty-two migration
assertions fail on immutable `2a170247ba3` and pass on the candidate. Eight
additional native replacement cases commit on that baseline but reject before
writeback/spend on the candidate, then recover the original journal result
without rerunning the Host and debit once. These cover ordinary and final
completion on both backends; they do not establish live model or installed
consumer adoption.

Three balanced sequential cohorts use clean sibling source copies and the same
fixtures. Each warm helper row has 93 samples per arm. Complete intent/recovery
classification changes from about 0.002 ms with no bridge call to these
median/p95 values: File 19.813/26.454 and 20.770/25.287 ms; SQLite
20.243/23.964 and 19.005/22.458 ms. Each adds one logical request with
43,200/43,253 request bytes respectively and 1,693 result bytes. Historical
classification retains zero requests and equivalent outcomes. Logical JSON
counts exclude framing and physical transport retries.

The same cohorts also execute real CLI admission, capture, synthetic Host work,
independent validation and settlement, followed by native Todo readback and
committed replay. Each row has only three samples per arm; the table reports
median and observed range, not a p95 or causal speedup:

| CLI completion | Baseline median [range], ms | Candidate median [range], ms | Logical requests, base → candidate |
| --- | --- | --- | --- |
| File / ordinary | 2303.0 [2001.9–3646.5] | 2173.5 [2051.4–2196.6] | 282 → 283 |
| File / final | 2306.0 [2283.4–2389.4] | 2818.4 [2338.2–2973.9] | 294 → 295 |
| SQLite / ordinary | 1888.5 [1758.5–2061.7] | 2513.5 [2086.5–2955.7] | 278 → 279 |
| SQLite / final | 2549.1 [2240.7–2681.2] | 2282.7 [2276.8–4064.8] | 294 → 295 |

Native outcome observations agree, including the original settlement identity,
done readback and one debit. Committed replay retains five logical requests and
zero new effects in both arms. Readback/replay observations are retained
separately from the timed execution. Background host load is uncontrolled;
no other owned validation runs alongside these cohorts. The added source check
is a demonstrated cost, while whole-CLI timing remains mixed and default SQLite
performance is not qualified. Models/cache tokens, billing, evaluation,
implementation/human costs, accepted real-work outcomes and original-quota net
benefit remain unqualified; earlier negative observations remain valid.

## Historical candidate block 3

The [numeric-integrity checkpoint](2026-10-29-complete-numeric-integrity.md)
binds producer number identity through one typed owner and all complete readers,
and removes an unused Host echo. The subsequent [sparse-transport integration](2026-10-29-sparse-numeric-integration.md)
adopts the existing context collector instead of retaining a separate complete-path
rule. Its changed experimental hash identity is explicit in codec v2; old bindings
retain unchanged captures and their pinned readers. Original capture-byte checks
remain separate. Component transport savings do not qualify original-quota outcomes,
full cost or a live writer cutover.

## Historical candidate block 4

The acceptance-sharing increment in #7430 moves the original Python display
algorithm into the existing TypeScript composition owner, before hashing/capture;
the old display helper now delegates to that same implementation. Complete readers
and historical staged reads use one reference resolver. Equal contracts retain
one full owner; different revisions, JSON types, provenance and negative evidence
remain distinct. Existing references follow relocated owners. Dangling, cyclic
or unavailable references reject even with valid integrity hashes. Small objects
stay inline when a reference would cost more.

Isolated File/SQLite quota and native-host fixtures cover complete acceptance at
the actual synthetic Codex subprocess input, failed-attempt recovery and original
once-only settlement; shared full/delta/lost-baseline reconstruction is also
exercised. These qualify transport semantics, not model compliance. The legacy
display adapter adds a transport call; complete composition shares inside its
existing call. No latency improvement is claimed.

A fixed-source acceptance fixture with four identical carriers produces 23,432
compact UTF-8 JSON bytes for the fused contract versus 22,237 for the original
non-staged CLI (+5.37%). With capture staging, fused bytes are 23,515 versus
24,509 for original entry/review/writeback together, excluding extra material
reads. The original display-sharing helper is frozen at `895f38fc5` for this
comparison. The acceptance-free workload above remains unchanged. This absorbs
an existing quota efficiency into the common path; it does not establish an
advantage over quota or full-cost benefit. The subsequent guidance increment below narrows that gap; next qualify the remaining
vision sharing in its existing owner after the title increment below, before deferred-detail choices. Keep cold and staged totals separate and retain negative results; do
not force a byte target by dropping unique instructions.

The next #7430 increment shares replan vision-authoring instructions through
that same TypeScript composition boundary. Its retained owner is the existing
durable-writeback settlement step. The legacy Python display helper delegates
sharing and only adds its historical presentation metadata. The complete
reader and historical unstaged/staged adapters resolve the same exact reference;
no second authoring policy is introduced. JSON type, revision or instruction
differences stay inline, and a reference must be smaller. Conflicting inline/ref
values, wrong step kinds, missing targets and chains reject even if hashes match.
The settlement plan, all original instructions and the example remain intact in
the capture; the historical writeback stage retains its existing example omission.

Two old CLI spellings now expose the same shared complete contract; material
readback, rejection before effects and native input are qualified separately.
The exercised native outer-controller inputs lack an inline settlement authoring
owner, so their authoring
remains inline; sharing never invents that owner or transfers settlement authority.
At the authoring checkpoint, four legacy projection tests expected multiline-title
or Explore-guidance sharing absent from the complete candidate and failed at both
the preceding and authoring heads. The guidance increment below resolves two;
the title increment below resolves the other two. Historical failures remain recorded, not waived or relabeled.
These slices do not qualify installed App adoption.

For the fixed synthetic authoring fixture, the original non-staged CLI is 22,062
compact UTF-8 JSON bytes and the fused contract is 23,266 (+5.46%). With capture
staging, original entry/review/writeback total 24,311 versus 23,349 fused bytes.
The original CLI projection module is frozen at `895f38fc5`; inputs and projected
read stages are held fixed. Additional reads, request framing, tokens/cache,
latency and model outcomes remain unmeasured. Sharing eliminates a duplicate
implementation and duplicate instructions in the candidate, but does not prove
an improvement over quota's existing sharing or a positive full-cost diff.


The Explore guidance increment removes the Python exact-match loop and reuses the
same TypeScript composition boundary for complete and legacy display callers.
Only identical instructions with an existing durable-writeback Explore attachment
share that same-capture owner when the reference is smaller. Unique/short text and
missing owners stay inline. Invalid, conflicting or chained references reject;
no instruction is shortened and an optional attachment gains no new obligation.
Historical staged and unstaged readers resolve the reference before moving context,
then reuse Explore's existing domain projection. Independent Explore reads still
return full guidance. The compatibility helper retires with the legacy display
callers; it owns no separate decision or sharing algorithm.

Real isolated File/SQLite CLI captures now satisfy the two previously failing
Explore-sharing cases, including standalone material comparison, historical
readback and invalid-capture refusal without state/spend changes. Existing staged
CLI tests still exercise unavailable-registry reads, tamper refusal and actual
writeback/settlement. Delta reconstruction preserves changed instructions and
lost-baseline recovery; incomplete context falls back to full delivery. These are
transport and local integration results, not installed App or model-outcome proof.


Broader caller qualification also migrates old test assumptions to the admitted
complete contract: native subprocess capture/retry compares the entire original
contract, and generated settlement commands still settle only the original Turn.
Historical capability readers retain explicit canonical-fixture initialization,
refusal facts and scheduler information without granting execution authority.
Rejected capability admissions still emit their typed diagnostic/historical
projection, whereas admitted calls emit the complete contract. This remaining
failure-path migration belongs to C1 before full cutover; it is not a permanent
second contract. Synthetic native/stdio providers do not qualify model benefit.

With the same original projection frozen at `895f38fc5`, the synthetic guidance
fixture uses 22,848 compact UTF-8 JSON bytes on the original non-staged path and
24,320 fused (+6.44%). Captured original entry/review/writeback total 26,081 versus
24,403 fused bytes. The guidance-free fixture remains 20,543 versus 21,738 bytes
non-staged. Required extra reads, framing, tokens/cache, latency and accepted model
outcomes remain unmeasured. Shared code and restored quota information efficiency
do not establish a positive complete-cost diff; C1 remains open.

The current-Task title increment removes the Python exact-source matching algorithm.
Complete composition and the legacy display delegate now share its TypeScript
owner. Only an exact duplicate of `text` or `title` in the unique, verified,
complete current selected Task can become a same-capture `title_ref`; the full
original declaration and its provenance stay inline. Missing, differing,
incomplete, ambiguous, failed, done or archived sources do not qualify. The
historical character threshold, including Unicode code points, stays unchanged.
The old display-only label-prefix omission remains local and is not applied to
the complete contract. This delegate retires with the legacy display callers.

Complete readers validate identity and reference ownership again, rejecting
conflicting inline values, chains, malformed paths and unavailable targets even
with valid hashes. Historical envelopes retain the full Task source, without a
relocated dangling lane reference. Real File/SQLite CLI with differential delivery
off now passes the two previous multiline-title cases; captured variants also
verify exact readback and refusal before state mutation or spend. Delta and
lost-baseline/full recovery preserve changed requirements. Existing independent
Todo detail still returns the full declaration. This qualifies local transport
semantics, not installed App adoption or model behavior.

For a fixed synthetic current-Task fixture, the original non-staged CLI frozen
at `895f38fc5` is 28,585 compact UTF-8 JSON bytes and the fused contract is 29,780
(+4.18%). Captured original entry/review/writeback total 29,906 versus 29,863 fused
bytes. The no-current-Task control remains 20,543 versus 21,738 non-staged bytes.
These totals exclude request framing, extra material reads, tokens/cache, latency
and accepted outcomes. They restore quota's existing sharing in the common path;
they do not establish positive full-cost benefit.

The vision-audit increment now moves the nested exact-match algorithm from Python
into that same TypeScript owner. Complete and historical display callers share
only an identical full audit, including unknown facts, negative evidence and
ordered obligations, when its reference is smaller. Different JSON types are not
equal: the old Python `True == 1` comparison could erase a channel difference;
that case now stays inline. Short audits also stay inline. The root retains the
complete audit and judge; legacy display metadata remains in its adapter.

Complete recovery rejects missing, malformed, chained or conflicting audit/judge
references even with recomputed valid hashes. Existing historical envelopes still
obtain their judge from the inline root, so no moved reference dangles; their
existing projection limits are unchanged. Real isolated File/SQLite CLI captures
with an active prior vision verify the full judge, exact capture readback and
rejection before state mutation or spend, without differential delivery enabled.
Delta/full and lost-baseline recovery retain changed requirements. Fixtures with
no active vision correctly emit no audit and cannot qualify this path.

With the original CLI projection frozen at `895f38fc5`, the fixed synthetic audit
fixture uses 22,337 non-staged compact UTF-8 JSON bytes versus 23,333 fused (+4.46%).
Captured original entry/review/writeback total 23,658 versus 23,416 fused bytes.
The no-audit control remains 20,543 versus 21,738 non-staged bytes. These are not
tokens/cache, latency, extra reads, framing or accepted outcomes. Restored sharing
and fewer rule owners do not prove positive full-cost benefit. The legacy audit
delegate retires with its display callers; remaining consumer retirement and
packaged/outcome qualification still keep C1 open.

Rejected admission now uses the same complete composer before immutable capture
when the decision has an interaction contract, including an unhealthy paused
source. A false `ok` still prevents host handoff and native context-baseline
publication and retains the failing exit status. Composition grants no delivery
or settlement authority. Invalid complete sources fail validation before capture.
Errors before a work contract exists retain their original typed diagnostic and
recovery facts, with either CLI flag spelling; they do not acquire a manufactured
work contract or pass through the historical envelope renderer.

Isolated real File/SQLite CLI checks cover missing work, unavailable capability
and selection-required reentry under both App-heartbeat and generic profiles.
They preserve the original receipt on reentry and create no spend, baseline or
unauthorized state mutation. Capture consumers are being moved to exact complete
readback while keeping explicit historical long-route recovery coverage. This
qualifies local transport and refusal semantics, not installed App adoption or
all-provider/full-path delivery. Remaining consumer retirement and packaged
single-session outcome/cost qualification still keep C1 open.

Subsequent observation qualification separates two issues. #7464 repairs the
cache test's comparison of independent live observation clocks while retaining
all other facts. #7468 preserves the existing authority owner's typed rejection
and backup/migration recovery in `quota status/plan`, whose early failure return
had hidden that information. Unmigrated ordinary reads remain refused under the
accepted #7032 cutoff; File/SQLite retain their scope-isolation checks. The new
recovery assertions fail on the previous implementation and the corrected
scope/diagnostic/authority selection passes 156 tests. These independently
reviewable fixes are absorbed by the integration candidate, not evidence of
installed adoption or improved outcomes/cost. Earlier failed runs remain
historical evidence; these diagnosed failures are no longer open investigations.

For an unhealthy-paused synthetic source, the original CLI projection frozen at
`895f38fc5` uses 6,325 compact UTF-8 JSON bytes and the complete contract uses
6,639 (+4.96%). The extra binding preserves common validation; this is no efficiency
win. Tokens/cache, latency, extra reads, framing and accepted outcomes remain
unmeasured for this case. No live model experiment is implied by these checks.

The shared reference owner is merged in #7457. Its
Task-title and Explore operations use one coarse legacy projection call; complete
composition reuses those same functions inside its existing call. The integration
branch absorbs the prerequisite commits rather than carrying a copied owner.
New `should-run` emission accepts the already-composed contract or original
predecision diagnostic only; it no longer falls back to legacy packet/envelope
projection. Historical capture recovery remains explicit. Component measurements
show reduced bridge cost versus two calls, but higher cost than the original
Python projection; this is not evidence of a positive full-path quota comparison.

The [packaged heartbeat comparison](2026-10-28-packaged-heartbeat.md)
now exercises real isolated File/SQLite CLI cold, warm and same-capture recovery
against original quota. The first result grew default context and stdout. The
follow-through shares one typed diagnostic owner across legacy and scoped native
callers and removes the replaced Python algorithms. Its independently usable
legacy-reader slice is proposed in [#7557](https://github.com/loopx-project/loopx/pull/7557);
the integration uses the identical module. Unknown fields/lists, complete retained
records, candidates and portfolio remain intact. The repeated comparison reduces
cold/recovery context by about 5.3 KB, but warm context still grows about 2.8 KB;
all-peer expansion still adds 13,940 bytes and ten CLI calls. The public v4 budget
probe still fails on both preceding and current integration wheels. No positive
complete-cost diff, default performance or model benefit is qualified. Continue
within C1 on warm reconstruction/metadata cost and remaining consumers; this does
not promote the candidate by default or qualify the separate controller experiment.

The integration also adopts the merged [#7528](https://github.com/loopx-project/loopx/pull/7528)
overlay in that same context codec. Relocated exact evidence can share one base
subtree while current and unknown controls remain literal, including through the
scoped C1 wrapper. The ledger separates the positive relocation-only byte result
from the repeated packaged comparison: ordinary warm input still grows about
2.8 KB and the v4 budget failure remains. This removes a demonstrated transport
redundancy without establishing a positive full-cycle or outcome result.

These cost and budget results remain pinned to their ledger revisions. The
[later mainline integration](2026-10-28-packaged-heartbeat.md#mainline-integration-and-immutable-reader-identity)
repairs native projection and original-capture reader wiring; it does not rerun
or qualify those model/full-cost comparisons. The subsequent
[signed-material checkpoint](2026-10-28-packaged-heartbeat.md#signed-complete-material-staging)
extends the same material owner to validated complete inputs and preserves
original detail positions through scope filtering. This removes that consumer's
full-material fallback when staging earns its transport bytes. It does not
change the Host's default reading policy or qualify model retention, full costs,
remaining caller/writer migration or historical-reader retirement. C1 and its
original quota-baseline gate stay open.

## Historical candidate block 5

### Local registered Goal material checkpoint

The complete Turn writer and existing native context-delivery opt-in now request
full registered Goal material before capture. The source adapter returns original
UTF-8 text for unmarked or mixed files; the existing TypeScript context owner
checks the full-file revision and fulfills that read. Existing marked-region
lineage and canonical Todo authority remain. Ordinary quota with delivery off
keeps progressive reads. The bridge requires a `complete_goal_v1` acknowledgement;
an older runtime cannot silently ignore the request.

Automatic business-work Host context uses `host_dispatch_context`, which refuses pending or
invalid Goal material before provider loading. Historical `host_context` metadata
reads remain available for original-version interpretation and necessary result
finalization; stored captures are not rewritten. This is a bounded material
delivery prerequisite. General capability-read interlocks, current-condition
revalidation, packaged adoption, real-model adherence and complete costs still
need their own evidence. Replan material-reading invocations retain their staged
reader and work restrictions. C1 remains unqualified.

### Current preference initial-read integration

The C1 candidate incorporates the existing selector repair from
[#7935](https://github.com/loopx-project/loopx/pull/7935): the initial read carries
the preference owner's complete current scoped view, including corrections,
retirement, empty replacements, provenance and advisory authority. It reuses the
same selector and source owner. It does not change activation, grant authority,
or replace a later action's required fresh scoped read.

Integration checks reuse the native capture reader and executor projection,
rather than requiring the retired capsule at the complete-source root or treating
navigation as an inline decision. The checks retain the delivery-off progressive
Goal read and verify the native default's complete inline Goal, canonical task,
later requirement change, damaged preference storage and signature refusal.
Five old layout assumptions fail on the pre-integration candidate too; after
adapting the tests, six complete modules pass 143 cases. Actual native exec and
CodeMode paths pass two cases with a deterministic loopback provider; six
TypeScript modules pass 124 cases. These counts describe those scopes only.

The complete CLI budget module still has four failing test groups, 39 passes
and one environment-dependent skip. Its observed small Markdown, crowded plans
and compatibility-view limits remain exceeded; the original five size holds and
their historical measurements are retained. No ceiling, fixture workload or
production prompt was reduced to hide a failure. This is source integration,
not a passing whole-C1 quality receipt, merge readiness, installed adoption,
natural-model behavior or a full-cost benefit claim.

### Canonical Todo projection cost qualification

A matched observation at main `f941b5939` and C1 `c59777597` uses the existing
`todo project-markdown` operation with its freshly read provider revision.
File/SQLite each cover one Todo, 36 Todos with/without Vision and 18 Todos across
three agents: 16 projection transitions, 32 surface conditions and 64 before/after
quota/plan responses. Complete canonical rows remain identical within every
transition. Independent full-file and excluded-region hashes reconstruct the
delivered Goal narrative, including its final stop. Unmarked source remains full;
only the normal writer's generated regions become excluded. No source-adapter,
projection policy, output threshold or fixture workload changed.

The measured component is normalized parsed CLI argv JSON plus actual stdout,
including the emitted Goal `cat` command when that response requires it. At C1,
projection reduces a crowded plan observation by 7,698 bytes without Vision and
7,697 with Vision; the multi-agent plan decreases by 3,697. Small quota/plan
observations instead grow by 364/75 bytes. Projection itself costs 1,667–1,700
measured bytes; counting both complete canonical readbacks as one-time audit cost
moves the positive cases' conditional break-even to 7–9 repeated observations.
This is an accounting scenario, not a recommendation to regenerate each Turn.

The existing projection also benefits main. With both snapshots' generated Goal
regions, C1 plans remain 23–24 measured bytes larger; quota is 959 bytes larger in
the small case and 1,958/1,759 larger in the crowded cases without/with Vision.
The ordinary multi-agent quota observation supplies neither inline Goal nor a
Goal read in either snapshot, so it is excluded from Goal-equivalent cost claims.
These observations qualify the existing source/projection route, not a new C1
optimization, identical complete decision semantics, model adoption or full net
benefit. RPC, CPU, invoices, outcome value and natural-model use remain unmeasured.

The original unmarked-source budget cases and all five historical holds remain
unchanged. Crossing a limit on this separately projected workload does not repair
or relabel those failures. Continue through the existing writer/reader owner and
the original quota single-session acceptance; do not delete unmarked Goal text,
relax signed-source fallback or add another compaction algorithm for these bytes.

### Generic Host dispatch and saved-result recovery

The shared executor now calls the existing TypeScript `host_dispatch_context`
owner before a new Host attempt is recorded, confirmed or launched. A valid
complete-source commitment with an unfinished Goal narrative read previously
reached a custom callback or external argv through this generic path; named
adapters already had their own check. The repair adds no decision owner, state,
vocabulary, general capability-read obligation or activation setting.

Fourteen File/SQLite cases cover callback and real argv dispatch for valid,
pending-Goal and historical requests, plus saved typed-result recovery. The
pending cases have no Host effect; valid and historical controls reach the Host.
Saved results continue through their original validator without another Host
attempt. The existing staged replan material-read exception remains owned by
the same TypeScript rule. Two actual native exec/CodeMode paths pass with the
deterministic loopback provider. This is synthetic source-checkout qualification,
not natural-model adoption, installed behavior or whole-C1 acceptance.

The three complete capture, Turn-driver and manager-context consumer modules
pass 345 cases on the integrated source, including that new coverage. This does
not rerun every earlier adapter, budget or backend qualification.

A paired component observation adds one `host_dispatch_context` RPC for a new
complete-source attempt: normalized params JSON plus result JSON totals 42,771
bytes on File and 42,828 on SQLite in the fixed fixture. Saved-result recovery
retains its existing single lineage RPC and identical measured bytes. Historical
requests without a complete source add no RPC. The baseline function is read
from the frozen pre-repair commit and runs with the same owner and fixture;
this isolates the dispatch component rather than replaying a whole branch run.
RPC framing, CPU, model input, invoices, outcomes and full net benefit are not
measured. This correctness check increases fresh-attempt cost; it is not a byte
saving, a same-capture body-size measurement or a repair of the five size holds.
The shared existing phase/helper is sufficient; a second wrapper or policy owner
would add no bounded refactoring value. Writer/reader retirement and the original
quota single-session outcome and complete-cost comparison remain open.

### Goal refusal before automatic-start reservation

The generic dispatch check now runs after original-capture staging selects the
input and before automatic-start reservation, publication or new journal writes.
It uses the staged original plan when present and the current request otherwise.
The previous ordering could reject a pending Goal without launching a Host yet
still reserve its interval and write a journal/capture. A subsequent valid Turn
then waited for that interval. The same TypeScript rule is relocated; there is
no new rule, state, configuration, bypass, persistent cache or extra RPC.

Four File/SQLite refusal cases reproduce that waiting state on the frozen
pre-repair source; six valid/historical controls pass. The strengthened public
matrix covers callback and real argv, staged capture, actual cadence state and
saved-result recovery in 22 passing cases. Two actual native exec/CodeMode cases
pass with the deterministic loopback provider. Existing result recovery retains
its metadata/finalization route without reserving or starting another attempt.

The four complete capture, Turn-driver, manager-context and cadence modules
pass 357 cases. The first combined run retains 356 passes and one old root-layout
failure, also reproduced in a clean frozen pre-repair checkout. That cadence
test now reads its scheduler field through the actual immutable capture reader;
its original floor and refused-reduction assertions remain unchanged.

Ten paired synthetic conditions compare frozen executor functions with the
candidate through the same real owners, fixed source inputs and separate
equal-width namespaces. Socket observation counts complete JSON request/response
lines, including errors and framing, without retaining credentials. Refused
non-capture paths use 5 to 2 RPCs; capture paths use 6 to 3. Their measured bytes
decrease by 95,293/95,405 and 97,665/97,777 on File/SQLite, respectively. The removed
calls are one cadence admission and two journal writes. Refusal leaves no new
journal/capture and keeps the lane eligible; all four subsequent valid attempts
start where their frozen controls wait.

The six valid/historical conditions retain identical RPC counts and measured
bytes, with identical Host inputs after substituting only the separate capture
namespace. Complete-source dispatch validation still costs its existing RPC.
This is warmed RPC component qualification with an intentionally stopped Host,
not a whole-branch model/outcome experiment, same-capture body reduction, CPU,
invoice or full net-benefit result. The original five size holds and whole-C1
writer/reader and outcome acceptance remain open. Consolidating journal writes
needs its own interruption/recovery qualification; this ordering fix does not
coalesce the durable Host-attempt checkpoint or confirmation boundary.


### Current main integration and the original quota workload

The complete-source path now reuses main's Progressive and Delta owners. The
existing complete-decision owner checks the original source before presentation
plans are applied. Saved-capture transport options change only the derived
selection recipe; they do not mutate the signed original or create another
admission. The shared source-preserving edit transport retains sparse numeric
identity, current requirements, explicit-off behavior and same-capture recovery.
The replaced native-quota and bounded-settlement adapters are retired rather
than retained as competing production paths.

The original fixed quota task is repeated against main `24f46874bc97` and the
integrated runtime `675cd9d23b5b`: three repetitions of cold, warm and compaction
recovery on File and SQLite (36 conditions). All conditions produce the same
independently checked artifact and one debit, including replay. Both arms use
the same Python executable and unchanged workload/observer. The provenance
check covers 252 parent and internal-child observations. Counts include all
caller output, accounting-child RPC directions and observed Python regular-file
IO. Concurrent validation prevents a latency/CPU conclusion; native provider IO,
model input/cache/output, billing and natural effectiveness remain unmeasured.

| Provider / scenario | All caller stdout, main → candidate (bytes) | RPCs | RPC wire bytes |
| --- | ---: | ---: | ---: |
| File / cold | 55,754 → 35,834 | 242 → 209 | 1,468,459 → 1,474,720 |
| File / warm | 68,136 → 62,107 | 334 → 292 | 2,151,083 → 2,217,775 |
| File / recovery | 89,861 → 61,384 | 336 → 294 | 2,156,745 → 2,214,069 |
| SQLite / cold | 56,155 → 35,908 | 242 → 209 | 1,470,295 → 1,476,592 |
| SQLite / warm | 68,557 → 62,249 | 334 → 292 | 2,153,959 → 2,218,459 |
| SQLite / recovery | 90,349 → 61,522 | 336 → 294 | 2,159,660 → 2,216,837 |

These are medians, not an efficiency promotion. Warm output including baseline
preparation falls about 9%, but current-step output excluding that preparation
increases 7.0% on File and 6.2% on SQLite. Warm wire bytes increase about 3%;
observed Python read-plus-write bytes increase about 4%. The primary
same-capture full-to-selected-body reduction is about 87.8% on main and
82.6% / 85.4% on the candidate's File / SQLite warm paths. Candidate cold and
recovery select the full body (0% primary reduction); this is not zero net value.
Fewer RPCs and a complete checked source do not establish overall net benefit.
Original quota, writer/reader retirement and long-horizon acceptance remain open;
these observations do not erase earlier negative cohorts or frozen size holds.

The integrated source suites pass 448 complete-caller/settlement cases, 156
context cases and 14 work-acceptance cases; their five and ten skipped cases
remain explicitly outside this run. The two focused TypeScript modules pass
67 tests and the control-plane typecheck passes. The isolated installed wheel
passes 258 cases with one platform skip. Its 476 completed process observations
and 136,450 Core-module observations show no source-origin violations; abrupt
exit probes do not emit the audit's exit-time observation. All 1,897 compared
packaged source files match the measured product source. This qualifies the
packaged paths above, not live installation or the outstanding net-benefit gate.


### Selected-reader and settled-record transport

The explicit reader now sends only the source containers along the requested
literal escaped addresses. The existing TypeScript selector still validates
RFC 6901, array indexes, failed batches and their recovery parent; Python renders
the original digest-checked values. Array lengths, container kinds, initial
navigation, full discovery, historical pinned order and native section-read
verification are retained. No additional cache, metadata file, pointer parser,
read obligation or retention claim is introduced. Main's selected-source IO and
shared native replay owner are reused.

The combined refresh caller also requests a smaller final read from the existing
TypeScript settlement owner. After that owner verifies a settled Turn, this
explicit projection returns the same identity, controls and three receipts with
six unused Run/event bodies set to null. Default/full reads and all pending
recovery retain their complete original bodies. The optional internal RPC field
changes neither admission nor accounting, and adds no CLI or product workflow.

The unchanged original quota workload is repeated against main `886f2cdff4b2`
with the same Python, materials, independent artifact check and replay: three
repetitions of cold, warm and compaction recovery on File and SQLite, 36 conditions.
All produce the same artifact and one debit. The 252 parent/accounting-child
source observations are clean; the measured product source remains unchanged
through the run. Medians include both RPC directions and all required reads:

| Provider / scenario | All caller stdout, main → candidate (bytes) | RPCs | RPC wire bytes |
| --- | ---: | ---: | ---: |
| File / cold | 55,946 → 35,930 | 241 → 208 | 1,465,253 → 1,429,981 |
| File / warm | 68,364 → 62,287 | 332 → 290 | 2,143,831 → 2,147,306 |
| File / recovery | 90,181 → 61,556 | 334 → 292 | 2,149,657 → 2,143,784 |
| SQLite / cold | 56,343 → 36,004 | 241 → 208 | 1,467,027 → 1,431,735 |
| SQLite / warm | 68,793 → 62,429 | 332 → 290 | 2,146,719 → 2,149,912 |
| SQLite / recovery | 90,666 → 61,694 | 334 → 292 | 2,152,475 → 2,146,508 |

Warm RPC count falls 12.7%, but wire bytes still rise 0.16% / 0.15%. Current-step
stdout excluding preparation rises 7.0% / 6.2%; observed Python read-plus-write
bytes rise about 4%. The primary same-capture full-to-body reduction is 82.6%
for the candidate's warm paths versus 87.8% for main. Candidate cold/recovery
still select the complete body: 0% primary reduction does not mean zero net value.
The earlier topology-only cohort also remained negative; it is not combined with
this cohort. Lower cold/recovery transport does not establish overall benefit.
CPU/latency, native provider IO, model input/cache/generated output, invoices and
natural long-horizon outcomes remain unqualified. Earlier cohorts and frozen
size holds retain their original results; original C1 acceptance stays open.

Real public CLI replay preserves all 15 successful/rejected selection observations
against main, including exact large integers, escaped keys, invalid indexes and
whole-batch rejection. A dropped-source-structure mutation fails the independent
value oracle through that same entrypoint. The settlement suite checks complete
parity by default and throughout six recovery states; its 137 TypeScript tests
and control-plane typecheck pass. Final installed-wheel and premerge readback
are recorded with the submitted exact head, separately from live installation.

The final isolated installed wheel passes 325 tests with one Linux-only skip;
1,897 packaged source files match the measured product source. Completed-process
module-origin audits are clean; abrupt exits remain outside that audit. The
expanded source settlement run has 274 passes, four failures and one platform
skip. Both File and SQLite failures reproduce unchanged on main `886f2cdff4b2`:
a receipt-bound task becoming deferred returns `quota_skip` instead of its
original closeout recovery, and due successor-replan replay loses its action.
The initial report interpreted the guard's `should_run`/`delivery_allowed` facts
as conflicting. The follow-up below corrects that diagnosis while preserving
the failed run. The smaller record projection is not used on those failing paths;
no relaxed receipt rule is used to turn them into passes.

### Preparation and original-Turn recovery

An ordinary guard may require a claim while forbidding business delivery.
`should_run=true` and `delivery_allowed=false` are consistent in that state:
the existing claim postcondition requires same-Turn reentry and an explicit
`execution_preparation.state=ready`. Two earlier File/SQLite tests omitted that
step before producing their artifact. They now follow the existing caller
contract and verify append-only negative-to-positive guard lineage under the
same settlement identity. Neither the earlier negative event nor native result
admission is weakened or rewritten.

The other two failures expose a product bug. The selected-Todo IO adapter applied
ordinary execution preparation to a planning operation whose canonical
obligation already forbade delivery. A deferred task then suppressed the due
successor replan and lost its original action on replay. The adapter now consumes
that same explicit `delivery_allowed=false` fact used by the TypeScript
interaction owner before reading execution leases. Planning still permits no
ordinary delivery or debit. Omitted facts retain all existing preparation checks;
ordinary claims, conflicts, receipt identity and once-only recovery keep their
existing owners. No new decision owner, protocol field, permission, cadence
ordering or RPC is introduced.

With the same corrected test harness, frozen pre-repair product source passes
the two ordinary-delivery cases and fails both planning cases. The candidate
passes all four and the complete 30-case execution-preparation matrix. The
original four-failure cohort remains historical evidence; fixing caller ordering
and fixing product routing are distinct results. This closes the demonstrated
planning-route defect, not whole-C1 acceptance or overall efficiency.


The unchanged original quota task is also repeated against the same main source
for three repetitions of each File/SQLite cold, warm and recovery condition.
All 36 conditions preserve the independent artifact and one debit with replay;
252 parent/accounting-child source observations are clean and product hashes
remain unchanged. Warm RPCs are still 332 to 290. Wire bytes rise 0.165% on File
and 0.073% on SQLite; current-step stdout excluding preparation rises 9.5% and
6.2%, and observed Python read-plus-write bytes rise 4.1% and 3.8%. The primary
same-capture full-to-body reductions are 84.1% / 85.5% on the candidate versus
89.2% / 87.8% on main. Candidate cold/recovery still select the complete body.
This is a separate cohort, not a causal subtraction from the preceding cohort.
Planning-route correctness does not resolve the ordinary warm-path cost tradeoff.
Native provider IO, latency/CPU, model/cache/generated output, invoices and
natural long-horizon effectiveness remain unmeasured.

The expanded source settlement suite now passes 278 tests with one native
Windows PowerShell skip. A fresh isolated installed wheel passes 246 tests,
including the four corrected CLI cases, ordinary/native preparation, settlement,
combined closeout and runtime integration. Its Windows PowerShell and Linux
thread-group exit cases are skipped on this host. All 1,897 compared packaged
source files match, including the changed adapter; 227 completed processes and
102,420 Core-module observations show no source-origin violations. Abrupt exits
remain outside the exit-time audit. The original isolation guards are retained;
this does not install or promote the candidate on the active runtime. The shared
IO adapter is the bounded change; another contract projection or lifecycle
wrapper would add ordering risk without a required caller.


### Native quota capture publication

The qualified native quota caller now reuses the complete decision it has just
published as its source-recovery reference. Previously it wrote an identical
complete projection before asking the same TypeScript owner for optional
presentation. Thirty saved capture pairs in the preceding qualified workload
are byte-identical. The adapter now passes that producer-owned reference into
the existing native preparation; external continuity input cannot supply it.
Derived required views still receive separate captures when their content
differs. Standalone APIs and uninitialized native fallback keep their existing
publication paths. There is no hard link, persistent cache, new file format,
semantic owner, read obligation, permission or RPC. Existing historical paths
remain readable under their original identity and digest.

The original quota workload is repeated against main `6a9264034605`, with three
repetitions of File/SQLite cold, warm and compaction recovery. All 36 conditions
produce the same independently checked artifact and one debit including replay.
The 252 parent/accounting-child source observations are clean; product hashes
remain unchanged during measurement. All 30 candidate captures avoid the
redundant complete projection. This removes a duplicate producer write; it does
not omit the complete source or claim retained model input.

| Provider / scenario | All caller stdout, main → candidate (bytes) | RPCs | RPC wire bytes | Observed Python reads | Observed Python writes |
| --- | ---: | ---: | ---: | ---: | ---: |
| File / cold | 55,429 → 35,654 | 241 → 208 | 1,459,302 → 1,424,420 | 336,589 → 287,832 | 127,933 → 120,437 |
| File / warm | 67,723 → 61,768 | 332 → 290 | 2,133,937 → 2,137,552 | 552,218 → 511,741 | 238,580 → 201,925 |
| File / recovery | 89,316 → 61,059 | 334 → 292 | 2,139,532 → 2,134,660 | 580,809 → 519,604 | 233,054 → 195,818 |
| SQLite / cold | 55,826 → 35,728 | 241 → 208 | 1,461,076 → 1,426,174 | 344,133 → 295,172 | 129,145 → 122,147 |
| SQLite / warm | 68,136 → 61,910 | 332 → 290 | 2,136,689 → 2,141,039 | 560,254 → 519,489 | 240,008 → 204,526 |
| SQLite / recovery | 89,801 → 61,197 | 334 → 292 | 2,142,354 → 2,137,296 | 588,955 → 527,354 | 234,484 → 197,664 |

Warm observed Python read-plus-write bytes fall 9.75% / 9.53%, and writes fall
15.36% / 14.78%. RPCs fall 12.65%, while wire bytes still rise 0.169% / 0.204%.
Current-step stdout excluding preparation rises 7.02% / 6.24%. The primary
same-capture full-to-body reduction is 86.1% / 84.7% on the candidate versus
87.9% on main; candidate cold/recovery still select the complete body. Tool
stdout is not model-generated output. Native/provider IO, latency/CPU, model
input/cache/output, invoices and natural long-horizon outcomes remain
unqualified. Earlier cohorts retain their original negative observations;
these separate cohorts are not subtracted to claim a causal cost reduction.

The same test harness on frozen pre-repair C1 source retains four semantic
controls and fails four duplicate-storage assertions. The candidate passes the
full 265-case native/lifecycle/reader/cost suite; its final advertised-reference
mutation also rejects changed bytes without changing the native journal. The
64-case public capture/model/recovery run passes. A fresh isolated installed
wheel passes 493 cases with one Linux thread-group exit skip. Its 1,897 compared
packaged source files match the measured product, including both changed IO
adapters; 278 completed processes and 45,049 Core-module observations have no
source-origin violations. Seventeen undistributed tracked assets/dev fixtures
remain separately listed, and abrupt exits remain outside the exit-time audit.
This qualifies the packaged paths, not active installation or independent PR
approval. The bounded change reuses the existing recovery reference rather than
adding a writer, helper framework or second semantics owner.

### Settled result-value transport

The existing combined-closeout caller requests `include_record_bodies=false`
after the TypeScript settlement owner verifies the original identity and all
receipts. That projection previously removed the top-level Run records but
still returned the same writeback/spend bodies inside four result values.
Those values are unused by this caller. The existing bundle helper now clears
them in that already requested, fully settled projection; identity, receipt
payloads, failures and all other controls retain their values. Default complete
reads and every pending recovery retain their original bodies. The persisted
Runs, historical readers, accounting and public CLI result are unchanged.
There is no new option, schema, RPC, cache, Python rule or model-read obligation.

This is an intentional change to the existing internal opt-in read projection,
not to the default reader. The only production opt-in consumer remains the
combined refresh/spend adapter. The same TypeScript harness first rejects the
old duplicate-body behavior, then passes all 137 cases on the repair, including
complete-read and pending-state parity. Real File/SQLite CLI tests independently
check the omitted bodies, unchanged receipts, persisted readback and one debit
through replay. A test-local variable collision discovered after those new
assertions was corrected without changing an assertion or the product rule;
the failed cohort remains recorded.

The wider source run retains 276 passes, one native Windows rendering skip and
those two fixture failures; the corrected File/SQLite cases pass. The isolated
wheel retains 387 other passes, two platform skips and the same two failures,
then passes both corrected cases without a product change. Its 1,897 compared
packaged sources match the measured product, including the changed TypeScript
owner; 17 undistributed assets/dev fixtures are separately listed. Across both
wheel runs, 1,297 completed-process observations and 535,308 Core-module
observations show no source-origin violations. Abrupt exits remain outside the
exit-time audit. This qualifies the tested installed-wheel paths, not active
installation, independent review or merge authority.

The unchanged original-quota workload is repeated three times per provider and
scenario against frozen main `0423908e9cf4`. The candidate includes that target
and the earlier C1 changes. All 36 conditions reach the same independently
checked artifact with one debit, including replay. All 252 parent/child source
observations match their pinned arm, and product hashes remain stable through
measurement. Child RPCs are already included in parent accounting.

| Provider / scenario | All caller stdout, main → candidate (bytes) | RPCs | RPC wire bytes | Observed Python reads | Observed Python writes |
| --- | ---: | ---: | ---: | ---: | ---: |
| File / cold | 55,472 → 35,678 | 241 → 208 | 1,459,815 → 1,400,685 | 336,894 → 288,097 | 128,010 → 120,512 |
| File / warm | 67,892 → 61,813 | 332 → 290 | 2,135,559 → 2,114,908 | 552,831 → 512,164 | 238,851 → 202,786 |
| File / recovery | 89,386 → 61,102 | 334 → 292 | 2,140,392 → 2,111,166 | 581,330 → 520,030 | 233,207 → 195,945 |
| SQLite / cold | 55,868 → 35,752 | 241 → 208 | 1,461,586 → 1,402,351 | 344,435 → 295,437 | 129,221 → 122,222 |
| SQLite / warm | 67,403 → 61,955 | 332 → 290 | 2,136,570 → 2,116,685 | 559,999 → 519,924 | 239,399 → 203,919 |
| SQLite / recovery | 89,875 → 61,240 | 334 → 292 | 2,143,241 → 2,113,734 | 589,491 → 527,788 | 234,645 → 197,795 |

These are three-observation medians, not a significance claim. Warm wire bytes
fall 0.97% / 0.93%; the observed ranges are 2,134,771–2,135,559 versus
2,113,851–2,115,192 for File, and 2,136,570–2,138,375 versus
2,115,544–2,117,498 for SQLite. Warm Python read-plus-write bytes fall
9.69% / 9.45%, and RPCs fall 12.65%. These cumulative C1 comparisons do not
attribute the earlier RPC/IO reductions to this small projection change.

Current-step stdout excluding preparation still grows 6.66% / 8.73%.
The primary same-capture full-to-selected-body reduction is 84.7% / 86.1%
versus 87.7% / 89.2% on main; its three-repeat ranges are 84.6%–86.2% /
84.7%–87.6% for the candidate and 87.7%–87.9% / 87.7%–89.2% for main.
Candidate cold/recovery still select the full body. This projection changes
internal transport, not model-generated output. CPU/latency, native/provider
IO, model input/cache/output, invoices, natural adoption and whole-workflow
net benefit remain unqualified. Earlier positive and negative cohorts retain
their original versions and results; separate cohorts are not subtracted for
causal attribution. C1, its final reader exits and overall acceptance remain open.

During qualification main advanced to `7f550aef58a8`, changing the Chat
original-conversation fallback and its two frontend fixtures. That later
combination has not been qualified; the cohort and integration base remain
`0423908e9cf4` rather than being relabeled as the moving target.

### Main integration and independent context layers

Implementation `6f00db17c654` integrates main `918844a01746`. It retains the
invocation-local initial plan while adopting main's independent Delta path:
explicit Progressive off plus Delta auto does not publish progressive navigation
or observed-field reuse. Digest-bound file deltas and complete current recovery
remain available. Normal quota presentation and managed preview already consume
the shared complete-source owner; this integration does not repeat their cutover
or retire version-pinned historical journals. C1's RFC increments follow main's
annex layout without dropping either language's obligations.

Actual Codex with static loopback inference qualifies default Progressive and
independent Delta in both source and isolated wheels. Complete unchanged large
acceptance fields still reduce the matched warm selected BODY from 14,386 to
2,583 bytes; unverified observed-field continuity restores 14,386 bytes. The
independent-Delta case reconstructs every original field and reads complete
current recovery through the actual client. Saved-source identity refusals,
explicit off and lost-predecessor/compaction recovery also pass from the wheels.
These are transport checks, not a new model experiment or installed adoption.

A new matched engineering cohort uses the same synthetic three-step Task with
main's original quota product entry: File/SQLite, cold/warm/recovery, three
repetitions and the same checked artifacts and single debits. It records 36
scenarios and 60 Turns, including preparation and child RPC/IO once. BODY is
reconstructed from returned pages in byte-offset order, not the backing file's
size. SQLite warm current-step BODY is 23,617 → 22,970 bytes (−2.74%), CLI stdout
58,949 → 48,401 bytes (−17.89%), RPCs 294 → 290 and framed RPC bytes about −3.74%.
Including preparation, stdout is 115,568 → 93,613 bytes, RPCs 586 → 578 and framed
traffic about −4.01%; observed Python regular-file reads/writes fall about
8.80%/26.18%. Node/kernel/provider IO and model costs are not measured. File warm
instrumented CLI time increases about 7.5%; this is not latency qualification.
The lightweight quota fixture does not contain the large acceptance bodies, so
its result does not attribute savings to field reuse. It validates the measured
steps, not completion of the original three-step Task, natural model adoption or
whole-C1 net benefit. Historical failures and the remaining reader, quality and
independent-acceptance obligations remain open.

### First-read convergence after main adoption

The subsequent integration `339f01e08782` adopts main `39770a960483`, including
the landed first-read producers/readers. The last `project_codex_context` test
caller now uses `prepare_codex_context`; the unused Python wrapper is removed.
Recorded source versions and journal recovery codecs retain their separate
support obligations. Benchmark preflight tests follow the emitted first BODY
through EOF, verify its reference/source/selection digests and required fields,
and retain the complete original source instead of expecting a navigation reply.

A fresh, source-stable 36-scenario cohort against that main retains the same
synthetic Task, artifacts, single debits and cost boundaries. SQLite warm BODY
is 24,016 → 23,285 bytes (−3.04%), CLI stdout 59,898 → 49,136 (−17.97%) and RPCs
292 → 290. Framed RPC bytes **increase 2.93%**. Including preparation, stdout is
117,466 → 95,041 (−19.09%), RPCs 582 → 578, framed bytes **increase 2.14%**, and
observed Python reads/writes fall 9.12%/26.28%. These results supersede neither
the preceding cohort nor natural-model qualification. The current-main native
control still delivers 14,386 warm BODY bytes versus 2,583 with field reuse;
that large-field component is absent from the lightweight quota fixture.

Configured Python typing remains non-passing: the prior C1 has 4,809 diagnostics
and this integration 4,814. The five additions are in unchanged main-owned
modules, verified by identical Git objects; they are not waived. Reader exits,
independent review, installed adoption and whole-C1 net benefit remain open.

### Complete-source qualification and native execution ordering

The no-write behavior qualifier now accepts Core-validated complete v1 sources
in either experimental arm and reports the actual packet version. Retained and
counterfactual candidates use the existing complete composer by default; an
already captured complete source keeps its original host binding and settlement
composition. Raw fixtures use the Core's default composition, without inferring
a different binding from candidate metadata. The versioned v0 state matrix
remains an explicit historical control, including action aliases refused by the
current owner. This does not retire supported historical codecs.

Pair preflight checks both confidentiality boundaries before either actor call.
Complete candidates retain source/integrity validation and exact paired origin;
provider instructions and encoded scheduler scanning follow the actual packet
layout rather than the arm name. Current-format ablations are refused before
actors. Semantic-negative fixtures are changed at their original source and
composed by Core, so a validly bound pair still has to pass the independent
fixed strategic-chain oracle. Tampered bindings remain separate negative cases.

The integrated native Host exposed an ordering gap: its delivery guard required
the Task lease that the model callback would acquire only after launch. Explicit
non-report Tasks now enter the existing lease lifecycle before that guard; the
managed callback reuses and checks that same Task. Canonical missing work still
reaches the kernel's structured refusal, unavailable authority fails closed, and
a conflicting execution lease is not borrowed. Report and recorded-resume
paths retain their existing lifecycle. No guard or independent validator is
relaxed, and this does not qualify automatic selection without an explicit Task.

The frozen main integration first recorded 646 passing and 22 failing tests.
Development runs retained their 292/4 and 356/1 results; the latter caught an
early-lease exception replacing the missing-work refusal. After correction, a
new source-stable eight-module cohort passed all 357 tests, including the 61
native File/SQLite, original-session, HTTP, refusal and independent-validation
cases. The main conversation-binding TypeScript module passed 59 tests; the
five changed qualification Python modules passed typing. These are local
synthetic actor/provider checks, not live-model behavior or full-cost evidence.

A fresh verified Chat bundle and isolated wheel contain 1,815 Python/TypeScript
sources matching the tested product bytes. Installed complete requests, current
and explicit historical pairs, wrong-source refusal and the real adapter with
a local transport double passed with 300 observed LoopX modules and no checkout
Core imports. Native Host was imported, not exercised as an installed journey.
The first stale/missing-bundle build, harness preparation failure and historical
v0 large-integer source mismatch remain recorded; the legacy positive control
uses its original representable fixture, while current-format large integers
remain checked. No active installation, live model run, whole-C1 acceptance,
quality/readiness or net-benefit qualification follows from these results.

The subsequent direct-writer audit found the public task-orchestration smoke
still creating v0 input, and an unused scenario packet builder with no caller.
The smoke now composes each host's complete source with its original scheduler
context and reads back the final Codex and Claude Host requests. Both preserve
the complete source and primary Task selection, alongside the existing child
context and native-operation assertions. The unused builder is removed rather
than replaced by another unused abstraction. The explicit versioned v0 state
matrix and supported historical writer/reader remain. The audit of Python
callers in product code and examples now finds only that historical matrix
calling the retained writer; it does not certify dynamically constructed calls.

The public smoke passed before and after migration. Default configured Mypy
passed all 17 roots at the preceding `a706379f70` checkpoint and after this
change; this updates the current typing qualification without rewriting the
earlier 4,809/4,814 failures. These checks do not exercise real child creation,
prove live-model adoption or qualify a new installed package.

One source-stable four-module regression cohort passed all 247 cases, covering
the real Turn driver, the active portfolio sources, the retained historical
corpus and replan Host-context projections. This is a separate current caller
qualification, not an extension of the earlier 357-case or installed run.

### Installed native lifecycle after main integration

Integration `5091fb82a9` adopts main `9d276f6cb9`'s original-context answer
repair. One source-stable cohort of the native Turn Host and private-conversation
modules passed all 466 cases. After rebuilding and verifying the Chat bundle,
the same two modules ran against an isolated installed package with unchanged
fixtures, real File/SQLite state, native protocol and HTTP, original sessions,
independent Task validation, source/permission refusal and recovery. The model
and external provider remain synthetic; this is no longer an import-only native
check, but it does not qualify an active App or live-model adoption.

The first installed cohort recorded 459 passes and seven preparation failures:
the seven explicitly activated account-routing cases lacked their separately
distributed optional package. After installing its same-commit 0.15.3 wheel, a
new complete cohort passed all 466, without combining partial reruns. The first
failures and 61 audit-helper deprecation warnings remain recorded; the helper's
keyword argument was corrected separately from product code.

All 1,815 packaged Core Python/TS files and the optional distribution's 24 files
match committed and installed bytes. The existing private fault-process test
driver is outside this wheel qualification. Across 48 observed Python processes,
1,228 Core modules, seven optional-provider modules and four observed subprocess
source paths came from the installed distributions, with no checkout Core or
provider imports. Fixtures and installed sources stayed unchanged. Default
17-root Mypy and the integrated conversation-binding TS module's 60 tests passed.
Earlier 357/247-case and PostgreSQL evidence retain their own versions; these
synthetic correctness and provenance checks do not establish full-cost benefit.

### Visible Goal reads and declared acceptance audits

Integration `d80b3860ab` adopts main `ac62962255`. Independent review found
that visible Goal prompts added an unconditional full-capture prerequisite,
including ordinary/off inputs, and rejected three previously legal 4,000-character
configurations. Captured prompts now require the needed original sections;
ordinary/off prompts gain no capture prerequisite. Work-context and required
reads, explicit full-read duties, original identity/recovery, transport flags,
settlement and stop rules remain. The 4,000-character host limit is unchanged.
Across 24 matched builder inputs, main accepts 21 and the candidate 23, losing
none of main's legal inputs; one oversized input remains rejected in both.

Both complete structured and Codex Host inputs now share the declared audit's
original trigger: read it before judging Goal acceptance or proposing closeout.
The eight File/SQLite counterfactuals cover both hosts with and without an audit;
absence tests caught and rejected an initial unconditional instruction. Twelve
synthetic historical structured-renderer cases retain exact output and input
parity. This is scoped instruction qualification, not model-retention proof.

Heartbeat consumers now validate the complete source and read the caller-owned
original capture through its digest-checked paged reader. Original scheduler,
selection, receipt and once-only settlement oracles remain. Installed execution
exposed a test caller's implicit checkout working directory: refresh must run
from its actual disposable project. The first installed cohort recorded 60
passes and 18 failures; an unchanged-wheel control passed after correcting only
that caller directory. No production workspace guard was weakened.

The final source-stable nine-module cohort records **855 passes, two failures**;
the complete three-module installed rerun records **80 passes, two failures**.
Both failures retain the original raw-JSON budget: 5,519 characters exceeds
5,400 for the two long-scope backend cases, whereas matched main passes all
four original budget cases. No budget, fixture size or useful duty was reduced
to hide this regression. Earlier failed cohorts and a private audit-directory
preparation failure remain recorded. Default 17-root Mypy, six changed-path
Ruff checks and the limited semantic advisory pass. All 1,815 Core wheel sources
match the frozen tested and installed bytes; five observed Python processes,
1,179 modules and six observed subprocess source paths show no checkout Core
imports. Some children override the audit environment, so this is not an
observation of every process. These synthetic checks qualify the repaired
Goal/audit/caller slice; whole-C1 acceptance, the remaining budget regression,
live App/model adoption and complete-cost benefit remain open.

### Preserve producer-bound heartbeat Turn identities

Runtime `3736048363` removes an inapplicable wake instruction after the existing
producer has actually replaced the timestamp placeholder. A bound prompt already
contains its original concrete assignment and separate guard; asking the model
to replace a new wake timestamp is redundant and risks losing that identity.
Reusable unbound templates retain the original guidance. The existing binding
owner handles all four prompt modes; no new field, switch or reader is added,
and saved bodies, commands, transport flags, required reads and recovery/stop/
settlement rules are unchanged.

Eight mode/shell counterfactuals fail before the repair and pass afterward;
Windows syntax is emulated, not an installed Windows qualification. Thirty-two
actual-builder characterizations retain exact unbound default/off output in
16 cases and remove only the fulfilled instruction in 16 bound cases, with
unchanged other generator fields and limits. The original four real-CLI budget
cases retain their paths, scopes and assertions: the long cases change from
5,519 to **5,379 raw JSON characters**, removing 139 body characters and one
additional JSON escape character. Matched main remains 5,363. This repairs the
candidate regression; it does not establish a whole-workflow saving over main.
The 5,400/5,500/4,000 limits and independent overflow cases remain unchanged.

A new source-stable nine-module cohort passes all **865** cases; a complete
three-module isolated installed cohort passes all **90**. These include the
original worker selection, repair, capture, replay and once-only settlement
oracles. Earlier 855/2 and 80/2 cohorts retain their original versions. Default
17-root Mypy and changed-path Ruff pass. Fresh bundle verification and wheel
source checks bind 1,815 Core sources and 24 optional-provider files to the
frozen tested bytes; five observed Python processes, 1,179 Core modules and six
observed subprocess paths show no checkout Core imports. The same audit-coverage
limits, synthetic providers, unqualified live-model adoption and complete-cost
claims apply. Independent review and whole-C1 acceptance remain open.

### Visible host scheduler observations

Pi, OpenCode and OpenCode 2 now read the existing scheduler observation for a
bound Agent. The same Core decision supplies the runnable state, reset token,
wait limits and terminal-source proof. This replaces those callers' ordinary
model-context reads, which could return a progressive navigation reference
without the fields their scheduler needs. OpenCode and OpenCode 2 read the
current cold-path cadence and still accept the historical cadence shape. Their
explicit host poll receipt uses its existing writer once per probe.

The observation has no Turn, capture or settlement effect. Work delivery uses
its original context policy. Older host bindings without an Agent ID use the
explicit full observation until they acquire an Agent binding. Keep the host
adapter and CLI on the same build when upgrading or rolling back. The retained
original-version Turn readers still cover outstanding work in their supported
recovery window.

### Recover rejected complete-source composition

Failed composition now records the existing transport-failure contract at the
capture root, with `ok=false` and `should_run=false`. Every original input fact
is retained under `source_diagnostic`, beside the exact original receipt and
the runtime's typed composition diagnostic. Both semantic rejection and runtime
startup failure follow this path. The regular digest-checked selected reader
provides diagnostic recovery.

Current-source consumers require a successfully composed decision before
publishing work or native claim preparation. This closes the path where a
rejected current input could be mistaken for an unversioned historical capture.
Genuine historical captures use their explicit recovery readers. A failure in
delivery after successful composition retains the validated original capture.

Focused synthetic File/SQLite cases cover unknown actions, unknown interaction
versions and composition unavailability after admission. Their independent
oracles check full diagnostic readback, rejected execution projection, native
journal and claim-source effects, one original receipt and zero spend. Existing
pause, capture, handoff and historical recovery coverage qualifies the adjacent
boundary. Whole-C1 acceptance and live-model effectiveness remain open.

### Codex SSH guard and managed retry

New Codex SSH Goal prompts and onboarding guards select the complete Turn
source with the existing native begin-Turn identity. A saved bootstrap that
invokes the generator with the previous display argument can still regenerate
its guard. Explicit context-delivery Off returns the complete inline source.
With delivery enabled the prompt follows the returned same-capture reader and
the original Turn's settlement plan. The full and standalone installations
execute the owed quota command through their shared CLI entry and grammar.

Managed-step requires a fresh complete decision at the TypeScript controller
boundary. A saved failed Turn journal still uses its original decoder. A
signed v0 preview injected at the live boundary is refused before launching the
Host, modifying the journal or spending quota. Retry uses the original Turn.

Source and isolated-wheel File/SQLite cases cover enabled delivery, explicit
Off, regeneration from the old bootstrap, same-capture reading and settlement
replay. The native Turn test also covers a lost spend response.

### Benchmark and MCP live reads

New benchmark heartbeat workers request the complete decision with either
context-delivery setting. Fresh checkpoint selection requires its signed source.
Historical checkpoint recovery uses its original capture reader and Turn
identity. The explicit v0 benchmark display still needs caller retirement.

The shared MCP live read requests the complete decision and returns its original
JSON plus the MCP guidance block. A missing or altered source binding is rejected
before delivery. The live unversioned MCP projection is removed. The existing
`complete_task` transaction continues to own claim validation and settlement.
The real stdio boundary, replay and historical recovery tests run on isolated
authority. Installed MCP clients still need end-to-end readback.

Ordinary quota, installed App adoption, the remaining settlement callers,
and single-session outcomes and full costs remain open.
