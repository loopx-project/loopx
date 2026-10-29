# Numeric integrity for complete contracts and Host transport

中文镜像：[同名检查点](2026-10-29-complete-numeric-integrity.zh-CN.md)。

This is proposed C1 integration in [#7430](https://github.com/loopx-project/loopx/pull/7430),
not a mainline or installed cutover. The comparison starts at
`4173cc2ee7ef94de00ca777ff8600455c080c29f`, after integrating main
`33d9ecd9fd29bce4986da6c9e9a6b758a24f91b3` and the preceding source-comparison ledger. Numeric binding is
implemented at `974cd09ef7cb12832911ee58880aaf25e86fb2b2`; the final runtime
`e18d46db51c58ee17c770d7bba915f0133cf9f78` also retires the unused source echo.

## One source identity through actual consumers

The preceding [packaged comparison](2026-10-29-packaged-source-comparison.md)
showed that a complete contract accepted `1.0 → 1` while its original capture-byte
check refused the changed file. The new experimental binding explicitly declares
`hash_codec=context_source_numbers_v1`. Its source and content commitments use the
existing TypeScript source identity, including sparse producer numeric encodings.
These distinguish integer/float values, negative zero and large integers that a
JavaScript parse can otherwise merge. Exact byte layout and alternative spellings
of the same producer value remain the separate capture-byte boundary.

| Consumer / owner | Convergence |
| --- | --- |
| `diagnostic_projection.ts::contextSourceIdentity` | Track retained or relocated values; removed carriers leave no numeric tokens in the projected identity. Existing callers without metadata retain their canonical identity. |
| `turn_envelope.ts` composition and complete validation | One numeric-aware binding, reused by replan reads, capture binding, Host checks and driver lineage. The complete path no longer recomputes a normalized whole-decision hash at each seam. |
| Python Turn transport | Derive sparse numeric encodings from original values and apply the existing source edits. No second digest, selection or recovery rule. |
| `host_candidate.py::complete_host_input` | Consume the existing compact `host_context` check. Delete the unused `validate_complete` source-echo operation; retain original model-source values. |

Missing/unsupported codecs or missing numeric transport reject before dependent
work. Older experimental complete bindings need their pinned reader; raw captures
are not rewritten. Historical v0 hashes, Turn keys and receipt decoding retain
their existing behavior. The binding adds integrity, not execution authority or a
new model-facing view.

## Qualification and remaining failures

Five numeric mutation cases fail on the unchanged baseline and pass after repair
across seven complete consumers. Additional tests cover unsupported codecs,
missing numeric transport, actual File/SQLite replan-file refusal/restoration,
malformed references, original-capture recovery and once-only settlement.
Capture rejection retains the original-observation diagnostic and error code.
The unified failure writer preserves the collection error without attempting a
legacy runtime renderer or manufacturing a complete contract.

The affected Python ladder passes 457 cases at `974cd09ef`. On the final runtime,
the retirement ladder passes 220 cases; one new assertion initially reversed the
existing unsupported-operation message wording, and its corrected case passes
separately. The final TypeScript ladder passes 149 cases. Touched-file typing
passes; a broader eight-file check still has 27 inherited errors versus 32 on the
baseline, with no new errors. This is not full typing qualification. The initial
442-pass/13-failure integration run is retained: it exposed capture diagnostics,
old fixture hash arguments and an obsolete failure-format assumption. The
corrections retain the original semantic refusal assertions.

After integrating main `3b4641c4fc0ec314d347f74b05880bb516012049`, product
sources remain identical to the final runtime above. Native file-manifest,
selection and compaction/recovery tests pass 198 cases; 46 native-binary opt-in
cases remain skipped. The external scheduler/worker ladder passes 183 cases.
Ark Host passes 39 local protocol fixtures with `arkruntime` 0.8.0 and mock
transport in a private dependency target. Resolving the full optional SDK profile
was stopped after its service-dependency expansion; this selected fixture run
does not qualify that installation profile or a live provider. MemT Host fixtures
cannot collect because tokenizer/runtime dependencies are unavailable. Neither
optional live provider nor model behavior is qualified.

Both isolated Python 3.14 wheels contain 1,860 product files matching their pinned
source, with generated frontend assets checked separately. The same synthetic
six-outcome/24-requirement workload runs through real File and SQLite CLI, native
hooks, compaction, corruption recovery, early-spend refusal and single settlement.
All 21 existing source/recovery predicates pass in every arm. The baseline still
accepts the numeric-only semantic mutation; the final runtime refuses it. Actual
emitted warm files reconstruct exactly; missing baselines and damaged deltas
recover the same full source. No live Goal, installed host or model is a fixture.

## Costs and next boundary

| Packaged observation | File: baseline → repair | SQLite: baseline → repair |
| --- | --- | --- |
| Cold saved presentation | 68,407 → 68,602 bytes | 68,515 → 68,706 bytes |
| Actual warm delta file | 3,755 → 3,775 bytes | 3,763 → 3,783 bytes |
| Complete observer CLI stdout | 494,448 → 495,559 bytes | 495,234 → 496,329 bytes |

Every arm makes 38 observer CLI calls plus one actual-delta read. Nine in-process
codec operations and one standalone integrity control are additional qualification
work, not a mandatory model reading policy. Package/fixture path-length differences
are included in these byte totals; the growth is not attributable solely to the
codec field. This C1-before/C1-after cohort does not replace the original-quota
comparison or the earlier frozen **25,862 > 13,000** failure.

For the standalone Host consumer, the same 68,227-byte synthetic source yields
one RPC in both versions. Request-parameter JSON grows 108,061 → 108,150 bytes;
result JSON shrinks 68,504 → 207 bytes. Original task, facts and constraints remain
exact; the new contract binding itself adds 41 bytes. Both installed wheels
reproduce this result. These are argument/result JSON sizes, excluding protocol
framing, composition/setup, model input processing, timing, memory and full Turn
cost. Deleting an unused echo does not establish a positive whole-path result.

Continue within the original C1 owner on remaining consumer/writer cutover,
historical-reader exit and comparable accepted outcomes with complete costs.
Keep existing independent source-fidelity PRs and their owners; this checkpoint
does not promote defaults, launch an experiment or close C1.
