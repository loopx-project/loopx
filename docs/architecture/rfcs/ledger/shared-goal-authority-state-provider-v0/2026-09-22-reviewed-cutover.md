# Reviewed cutover checkpoint

Moved without content change from [shared-goal-authority-state-provider-v0.md](../../shared-goal-authority-state-provider-v0.md) (former section "Reviewed cutover checkpoint") on 2026-09-26; RFC baseline `3e443ad7c`. Checkpoint records live in this ledger, not in the RFC body.

The saved-plan/recovery slice closes a concrete operator gap: execution can be
bound to the reviewed source/provider/policy, and a fenced cutover can be
completed or read back without reconstructing intent from legacy Markdown.
The TS owner shares durable qualification and exact receipt proof between both
paths. See [operation and acceptance](../../../../reference/reviewed-coordination-promotion.md).
This stage does not authorize an active Goal migration or flip a default.

Claim-preserving migration #4870 and reviewed cutover #4888 are merged;
shadow drain planning #4920 is also merged. Qualify their combined current head
for an existing claimed Goal rather than treating an old PR hold as current. Preserve the registered owners, existing claims and leases; do not
clear ownership to make storage migration appear ready. The saved-plan carrier
must retain migration strategy, registered-agent facts and target digest during
combined qualification.

The current seven-boundary plan above separates caller admission, executor
fences and snapshot reads; D2 and integrated migration may each split. This
portable-recovery slice contributes to migration qualification, not an entire
completed package. Use that single current plan instead of counting leaf fixes.
Actual elapsed soak cannot be compressed into a promised number of PRs.
PostgreSQL service admission and operations remain a separate medium-term lane.

现有 Goal 的可审核晋升与恢复、所有新 Goal 默认选用 provider、删除全部 Python，
是三个不同完成条件。先交付一条能保留状态、能读回、能恢复的真实迁移路径，再按调用方
闭合程度删除旧实现。不要用已合入 PR 数量替代端到端验收。
