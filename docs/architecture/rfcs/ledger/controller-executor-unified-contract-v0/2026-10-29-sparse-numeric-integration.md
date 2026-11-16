# Shared sparse numeric transport and an explicit binding transition

中文镜像：[同名检查点](2026-10-29-sparse-numeric-integration.zh-CN.md)。

This is proposed C1 work in [#7430](https://github.com/loopx-project/loopx/pull/7430),
not an installed or default-writer cutover. Runtime
`b9d1ce1847589de6e5ecab546b91631fb20e2083` adopts the collector and context
regressions from [#7661](https://github.com/loopx-project/loopx/pull/7661), commit
`3435ef65854ede411ca494f73da18b168d404362`. That independent prerequisite keeps
its original owner and review path; C1 does not copy it into a second collector.

## Reuse and compatibility

The existing Python IO collector now supplies metadata only for integral floats,
signed zero and integers outside JavaScript's safe range. Ordinary fractional
doubles retain their value without per-element path/token duplication. The same
collector serves context delivery, complete composition, capture/replan checks
and Host/driver consumers. TypeScript retains the source identity and hash owner;
no second Python digest, context selector, cache or recovery policy is added.
One local codec constant reaches both composition and validation.

Removing redundant tokens changes the earlier experimental hash identity.
Characterization confirms that merely substituting the sparse collector makes
an unchanged, valid v1 contract fail integrity validation. Fresh C1 bindings
therefore explicitly declare `context_source_numbers_v2`. Seven complete
consumer paths reject correctly signed experimental v1 input as unsupported,
before dependent work, rather than silently interpreting it under v2. The same
v1 artifact still validates with the pinned `e18d46db51c58ee17c770d7bba915f0133cf9f78`
reader and retains its exact bytes. Do not re-sign outstanding work to migrate it.
Historical v0 hashes, Turn keys and receipts retain their existing decoder.

## Matched transport evidence

Both isolated Python 3.14.8 wheels match 1,860 product files to their pinned
sources: baseline `e18d46db5` and runtime `b9d1ce184`. The cost probe feeds both
arms one immutable synthetic source, with 1,000 observations and one changed
value. It counts both JSON directions across 12 composition, validation, Host,
cold/delta and recovery calls. The matched safe-integer control remains unchanged.

| Complete-consumer workload | Baseline request / response | Sparse request / response |
| --- | --- | --- |
| Safe integers | 373,452 / 14,345 bytes | 373,452 / 14,345 bytes |
| Ordinary fractional observations | 944,548 / 14,345 bytes | 401,456 / 14,345 bytes |

The fractional combined component cost falls **958,893 → 415,801 bytes (56.6%)**.
The proportional-cost regression fails on the unchanged baseline and passes on
the sparse runtime. Original values, types and complete source remain exact;
binding codec and hashes intentionally differ. Counts exclude protocol framing,
setup, model processing, timing, memory, fees and human attention.

The first probe used separately generated sources with different fingerprints;
retain it as diagnostic evidence, not a matched comparison. The qualified repeat
uses one immutable source and checks all four composition-input fingerprints.
The public regression also constructs its paired source template only once.

## Real packaged recovery and remaining qualification

The same six-outcome/24-requirement synthetic workload runs through actual File
and SQLite CLI, native hooks, immutable capture checks, compaction recovery,
early-spend refusal and once-only settlement. All 21 existing source/recovery
predicates pass in all four matched-width namespace cases. Numeric semantic
mutation remains rejected in both versions. Reading the actual emitted warm file
reconstructs the current source; missing baselines and damaged deltas recover the
exact full source. Warm files remain **3,839 bytes (File)** and **3,847 (SQLite)**.

Each arm uses 38 observer CLI calls plus one actual warm-file read. Cold captures
are 68,934 → 68,928 bytes for File and 69,042 → 69,032 for SQLite; observer stdout
is 497,567 → 497,543 and 498,353 → 498,313. Runtime observation values still vary;
these tiny differences are not an attributed codec or model-input saving. An
earlier unequal-width namespace cohort is retained separately. This is a C1
before/after engineering cohort, not the original-quota outcome/full-cost gate.

The affected Python ladder passes 471 cases at `b9d1ce184`; after freezing the
test template, its two transport tests pass again with product code unchanged.
The selected TypeScript owner ladder passes 94 cases, TypeScript compilation and
Ruff pass, and the two explicit Python typing targets pass with silent imports.
Default import traversal still reports 5,487 errors in 623 files on both pinned
arms, with no new distinct error after line-number normalization; preserve that
failure separately from the earlier narrower 27-error cohort. The advisory finds
no supported vocabulary carriers, which does not cover the intentional literal
codec revision or all semantic forms.

Ark Host also passes 39 mock-transport protocol fixtures on this runtime using
the previously prepared selected SDK dependencies. Full optional SDK installation
and the unavailable MemT tokenizer/runtime profile remain unqualified; this does
not establish a live provider or model run.

The original **25,862 > 13,000** failure remains. Caller/writer cutover, old-reader
exit and comparable original-quota accepted outcomes with complete costs remain
open. No model run, live installation, merge, default promotion or new authority
follows from this checkpoint.
