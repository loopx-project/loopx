# C1 navigation convergence checkpoint

中文镜像：[同名检查点](2026-10-29-navigation-convergence.zh-CN.md)。

C1 subsequently adopts merged #7627's file-first delivery and file deltas at
target `385eb853c1d37f5a285f2e8e0b875bd0ea04d3b3`, preserving #7643.
The former `--turn-envelope` spelling no longer changes navigation or diagnostic
detail selection; both callers use the same owners.

C1 integrates target main `bcb1a8710844bda6b84c2a00cf5d86ba78e126d9` after
its numeric-reference checkpoint `16626b9d08b4017376093cff7c669241d68f99bb`.
This is an in-flight integration in #7430, not a new default or installed rollout.

The demonstrated conflict was two owners for the same reading decision: C1's
unpublished mandatory outcome scope versus merged #7560's advisory navigation.
The integration adopts main's navigation, complete authority snapshot and native
recovery-before-planning publication. It deletes the scope-only wrapper and
instruction rewriter, the dedicated ordinary outcome reader and fused native
operation. Ordinary detail reads reuse generic `context-read --select`; staged
replan retains its exact captured acceptance reference and shared index. It
introduces no authority store, reader, scheduler or approval gate.

Signed complete contracts remain unchanged in their original recovery capture.
They are not rewritten as partial signed decisions. Existing unsigned material
projection still retains cumulative requirements and defers supporting history
through the same exact-capture selector. File/SQLite integration verifies the
complete signed source, original final constraint, eight declaration sources,
selective readback and unchanged authority state. Interrupted optional planning
recovers the current capture without re-admission or settlement replay.

The discarded fusion used one owner call for a simple native projection. The
current path explicitly observes the existing planner plus codec: two calls.
The earlier call-count improvement and scope-specific size/timing results remain
historical; they do not qualify this integration. No latency, model-token,
accepted-outcome or complete-cost gain is claimed. #7627's merged source implementation is reused here; installed/model adoption
and outcomes still need separate qualification.

Validation and remaining gaps are recorded on the exact PR head. Preserve the
initial integration collection error, historical two layout failures and retired
reader-test failures as failed cohorts, then validate the current generic/staged
routes. Do not lower the original quota budget or treat changed test counts as
benefit. Numeric source/hash identity, writer/caller replacement, supported
historical-reader exit and original-quota single-session outcome/full-cost
comparison remain open. Optional two-session work remains separate.
