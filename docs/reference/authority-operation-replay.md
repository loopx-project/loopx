# Authority operation replay

File and SQLite accept a direct retry of an already committed operation when
its complete canonical body matches the original transaction. The body is
`next_projection`, `events` and `receipts`; JSON object key order is not intent.
`operation_id` selects that transaction within the opened Goal store.

This supports retry after a lost response. It does not create another state
transition, append events again, restore an old head or authorize another
external effect. If A committed, then B committed, replaying A returns A's
original cursor/provider revision while B remains the current head.

## Commit and recovery contract

| Case | File / SQLite | PostgreSQL / NoKV |
| --- | --- | --- |
| New operation, current CAS basis | Commit atomically | Commit atomically |
| New operation, stale CAS basis | `provider_revision_mismatch` | `provider_revision_mismatch` |
| Existing operation, identical body | `applied` with original cursor/revision | Ordinary commit remains a conflict; recover via `readReceipt` |
| Existing operation, different body | `operation_id_exists` | Conflict; normal revision-check precedence remains |

For a verified historical retry, File/SQLite do not require the caller's CAS
basis to remain current: they are returning a historical fact, not admitting a
new write. Validation, store identity/existing-only admission and the current
store integrity checks still apply. An invalid request fails before replay.
A matching operation with a changed projection, event or receipt is never
acknowledged as the original commit.

File reconstructs the original projection using the retained journal. SQLite
verifies the original checkpoint/delta window in the **same write transaction**
before acknowledging a replay. Comparing the caller to a stored digest alone
is insufficient: a damaged retained receipt/event must fail its own proof.
No full-history audit is added to ordinary retry. Digests detect inconsistent
bytes, not an administrator who rewrites both data and proof.

`CoordinationCommandReceipt` remains the provider-neutral business recovery
owner. It reads and validates the original command receipt even when a provider
returns `applied`, and reconciles conflict or ambiguous responses. Do not remove
that readback: `applied` can describe a historical commit, and the other
providers retain their existing direct-commit behavior. NoKV's ambiguous-write
readback recovery is distinct from its ordinary `commitAuthority` contract.

This changes File/SQLite's previous duplicate-commit rejection behavior.
Concurrent identical lease renewals can now both report `applied`, while only
one renewal/version transition is persisted. Business recovery still validates
request identity; a historical receipt never grants current lease authority.
There is no feature flag, new request field, storage migration or frontend
configuration. Reverting requires reverting the provider behavior, not merely
removing tests. Existing durable receipts keep their original format.

## Scope and verification

The shared-authority RFC owns this storage/recovery boundary. Goal lifetime
identity, lease epochs and provider revisions remain separate contracts.
This change does not implement Goal replacement isolation, semantic correctness
of model output, default provider activation or long-horizon qualification.
The [deferred Goal continuity note](../architecture/rfcs/goal-immutability-coherence-defense-v0.md)
preserves related restart, instance-replacement and constraint-recovery scenarios
under their existing RFC owners; those scenarios are not qualified by this PR.

`authority_operation_replay_conformance.ts` runs on both File and SQLite. It
checks full body drift, canonical key ordering, historical replay and concurrent
same-operation attempts against complete head, receipt and history readback.
SQLite adds retained-row corruption cases that must refuse replay without
changing durable rows. Existing real-process lease renewal and command receipt
suites cover the business entrypoint above these providers.

## 中文说明

File/SQLite 现在可以直接重试已成功提交的操作：操作 ID 相同，而且完整的
projection、events、receipts 相同，才返回原 cursor/revision。A 提交后 B 又提交，
重试 A 只确认 A 的历史结果，不把当前状态退回 A，也不再追加事件。

这不是绕过新写入的 CAS。新操作仍须匹配当前版本；历史重试则须证明原事务。
SQLite 在同一事务内校验对应 checkpoint/delta 窗口，不能只比较数据库中保存的
摘要字段。历史回执或事件损坏时必须拒绝，不能报告成功。

上层 `CoordinationCommandReceipt` 仍须读回并校验业务回执，处理响应丢失及不确定
提交；PostgreSQL/NoKV 的普通重复提交仍返回冲突。并发同意图续约可能从过去的
`applied/recovered` 变成 `applied/applied`，但实际只写入一次。历史成功不授予当前
lease 执行权。此变更没有新配置或存储格式，也不证明 Goal 实例隔离或模型语义正确。
