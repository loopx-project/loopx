# Canonical lease acquisition and lifecycle

An already promoted File or SQLite Goal runs `task-lease acquire`, `renew`,
`transfer` and `release` through TS-owned provider transactions. Fresh execution
and expired/ineffective-holder takeover now use the complete canonical head;
previously standalone acquire still entered the fenced legacy file writer.
Unpromoted Goals retain their legacy wire and transaction. A provider selector
does not promote a Goal or grant execution authority.

For a new execution on an open, eligible Todo with no prior lease:

```bash
loopx --registry registry.json task-lease acquire \
  --goal-id example-goal --todo-id todo_work --owner agent-a \
  --idempotency-key execution-a --expected-version 0 \
  --ttl-seconds 600 --write-scope 'src/**'
```

After expiry/release, use a new execution key and the current version from
inspect, rather than version 0. Active effective holders and overlapping scopes
on other effective leases reject acquisition. Archived, excluded, unregistered
or claim-conflicting holders do not block another eligible execution. The scan
uses all retained records, not the bounded operator display. Atomic
`todo claim + lease` shares the same facts, decision and record materializer.
Standalone acquire uses requested scopes; atomic claim retains its existing
Todo-required scope intent. Neither operation expands a permission grant.

## Operate the current lease

Read the current canonical lease and use its owner, execution key and version:

```bash
loopx --registry registry.json task-lease inspect \
  --goal-id example-goal --todo-id todo_work --format json
loopx --registry registry.json task-lease transfer \
  --goal-id example-goal --todo-id todo_work --owner agent-a \
  --idempotency-key execution-a --expected-version 3 \
  --new-owner agent-b --new-idempotency-key execution-b --ttl-seconds 600
loopx --registry registry.json task-lease renew \
  --goal-id example-goal --todo-id todo_work --owner agent-b \
  --idempotency-key execution-b --expected-version 4 --ttl-seconds 600
loopx --registry registry.json task-lease release \
  --goal-id example-goal --todo-id todo_work --owner agent-b \
  --idempotency-key execution-b --expected-version 5
```

The example assumes an active lease at version 3 and an unclaimed Todo or a Todo
already assigned to the eligible receiver. Without `--transfer-claim`, transfer does not reassign the Todo
claim. Neither form overrides an exclusion or widens write scopes. Use the actual readback
versions, not these example numbers.

## What inspection proves

`task-lease inspect` uses one TS read owner for legacy files and selected
File/SQLite authority. Service-owned PostgreSQL uses the same reader through its
existing identity-fenced factory; the CLI does not gain a PostgreSQL connection
or enable a service deployment. Promoted inspection reads Todo, lease and mode
from one complete provider revision, ignoring stale display and lease files.

`lease.status: active` describes the retained record. The top-level `active`
means its expiry is strictly after the observation clock **and** its current
owner is eligible for the active, open Todo. An archived record cannot revive
execution even when imported history retains `status: open` and a future lease.
Closed, unregistered, excluded and conflicting-claim owners return `active:
false` with the existing `executor_constraint` diagnostic. Rejection precedence
and diagnostic fields share the mutation-admission owner.

An active lease with an invalid expiry now fails with `corrupt_lease`, including
on the legacy route; it no longer looks like an ordinary inactive lease. Missing,
expired and released leases retain their previous successful inactive response.
For legacy storage, TS first checks the retained lease and only asks Python for
a full Todo projection when it is time-active; the final check re-reads the lease
after that projection. Inactive inspection does not parse the work history.
Registration-source receipts and the promotion fence are rechecked after the
read. Source changes trigger at most three host attempts, then an explicit
`authority_source_changed` failure. Selected-provider failures never fall back.

This is a read-only observation, not an execution grant or a lock across later
work. A subsequent registration change, provider commit or expiry can invalidate
it. Mutations must still prove their current owner/key/version and commit under
their existing fences. Inspection does not repair display, renew a lease, change
provider selection or spend quota.

检查由 TS 统一解释两条来源路径：`lease.status` 是保留记录的状态，顶层 `active`
还要求租约未到期、Todo 活跃且未关闭、owner 仍满足注册、排除与认领约束。
归档但仍标为 open 的历史记录不再产生有效执行资格；损坏的 active 到期时间明确
报错，不再伪装成正常失效。检查前后校验注册来源与晋升 fence，最多重试三次；
结果只代表一次观察，后续写操作仍须校验当前执行证明，不获得新的授权。

## Atomically hand over claimed work

When the current canonical `hard_lease` Todo and lease both belong to the sender,
explicitly transfer both in the same transaction:

```bash
loopx --registry registry.json task-lease transfer \
  --goal-id example-goal --todo-id todo_work --owner agent-a \
  --idempotency-key execution-a --expected-version 3 \
  --new-owner agent-b --new-idempotency-key execution-b --ttl-seconds 600 \
  --transfer-claim --format json
loopx --registry registry.json task-lease inspect \
  --goal-id example-goal --todo-id todo_work --format json
loopx --registry registry.json todo update \
  --goal-id example-goal --todo-id todo_work --agent-id agent-b \
  --note 'Continue the verified work' \
  --task-lease-idempotency-key execution-b --task-lease-expected-version 4
```

The sender must hold the exact active owner/key/version tuple and the open active
Agent Todo claim. Both agents must satisfy the existing registration, exclusion
and binding rules. A fixed `bound_agent` is preserved; this command cannot change
that binding to force a handover. An unclaimed Todo uses the existing lease-only
operation. The explicit option requires canonical `hard_lease` authority; legacy
writers reject it instead of attempting two separate writes.

One provider CAS commits `claimed_by`, source actor attribution, lease owner/key,
version +1, epoch +1, events and the original receipt. Todo ID, dependencies,
requirements, evidence and lease scopes remain intact. Existing continuation
notes retain their bytes but become stale when their bound Todo facts change.
A same-agent new execution advances the lease generation without fabricating a
claim edit. The old execution cannot update/complete the handed-over Todo; the
recipient still uses the ordinary proof-bearing update and completion commands.

`transfer_claim`, `claimed_by` and `todo_changed` describe the committed result.
The original request digest also binds the explicit option: adding/removing it,
retargeting or changing TTL on retry is rejected. Historical replay can report
the former recipient even after a later transfer or completion; inspect current
authority before starting a new operation. An ambiguous response must be retried
with the original arguments, including the original version.

The CLI drains the existing Todo projection after the joint commit. A display
failure reports `projection_delivery=pending` with `retry_business_mutation=false`;
repair the display and use `todo project-markdown` or retry the original transfer.
Recovery renders the current canonical head, never reinstates the historical
claim. Omit the option to retain lease-only behavior. To transfer back, the current
holder issues a new transfer with a new execution key and current version; do not
restore old files or receipts. This is an ownership transaction, not automatic
context delivery, peer acceptance, capability authorization or an external-effect fence.

| Operation | State change | Admission |
| --- | --- | --- |
| Acquire / takeover | Version +1 and epoch +1; new execution identity and expiry | Open active Todo, registered eligible actor, no effective conflicting holder or overlapping execution; optional version CAS |
| Renew | Version +1; owner/key/epoch/scopes unchanged; expiry is runtime clock + TTL | Active lease, current proof, registered eligible owner, active open Todo |
| Transfer | Version +1 and epoch +1; replace owner/key; retain scopes; set expiry | Active lease, current proof, registered sender and eligible receiver; new execution key |
| Transfer with `--transfer-claim` | Same lease transition plus atomic Todo claim/actor update | Canonical hard lease; matching source claim/proof; both actors satisfy Todo scope |
| Release | Retain version/epoch; persist released status and timestamps | Current owner/key/version proof; expiry, removed registration and closed/archived Todo do not prevent cleanup |

A missing lease at expected version 0 or an already released matching lease
returns a durable `no_change` receipt. Its storage cursor can advance while
lease state, timestamps and domain events remain unchanged. A wrong version or
wrong proof never becomes successful cleanup. Acquire/renew/transfer reject archived
Todos and safe-integer generation exhaustion before writing. The generation
check also protects the legacy path; release at that generation remains legal.

## Commit, retry and readback

One provider CAS commits the lease projection, event and original receipt.
Maintenance operation identity includes operation, Goal, Todo, owner, execution
key and expected version. The immutable request digest additionally binds TTL and the
transfer receiver/key. A retry with changed intent is rejected. The shipped
renewal receipt schema, identity and digest encoding remain compatible.

For maintenance, `status=replayed` and `idempotent=true` return historical results even after a
later renewal, transfer, release or expiry. They do not grant present execution
rights or renew again. Freeze the original request after a lost/ambiguous
response; recover its receipt, then inspect current state before new work.

Acquisition identity binds Goal/Todo/owner/execution key; the request digest
also binds original expected version, TTL and scope set. An exact retry of
`--expected-version 0` recovers its receipt instead of failing against the
version it created. A changed request under that identity is rejected.
Acquisition **success requires current proof**: an active, still-eligible lease
with the same owner/key/epoch. Replay after renewal returns the current
version/expiry in `lease`, with the unchanged original decision in
`original_receipt`; `current_provider_revision/current_cursor` identify that
readback. A transferred, expired or released execution cannot be revived by its
old receipt. Atomic `todo claim` with `--task-lease-idempotency-key` now uses the
same current-proof owner after commit/recovery, including receipt-only no-ops.
A plain claim without an acquisition request and maintenance receipts retain
historical semantics; they do not grant new execution.

For atomic adoption, freeze both identities across an uncertain response:

```bash
loopx --registry registry.json todo claim \
  --goal-id example-goal --todo-id todo_work \
  --claimed-by agent-a --agent-id agent-a \
  --claim-operation-id adopt-work-a \
  --task-lease-idempotency-key execution-a --task-lease-expected-version 0
```

The operation id identifies the claim transaction; the execution key identifies
the lease generation. Repeating this exact command after renewal returns the
renewed lease and the original receipt. After release or expiry it fails with
`idempotency_key_reuse`; after a claim transfer it fails current-owner admission.
Inspect the current state before choosing a new execution key and operation id.
If the receipt exists but the current head cannot be read, the command returns
`ambiguous` with same-operation recovery, not the historical active lease.
Switching away from `hard_lease` also invalidates atomic claim/acquire success.

| Readback | Meaning | Next action |
| --- | --- | --- |
| `replayed`, same epoch, newer lease version | Same execution was renewed | Use the returned current lease version. |
| `idempotency_key_reuse` | The receipt belongs to a retired execution | Inspect, then request a new execution with a new key. |
| `owner_conflicts_with_claim` | Current Todo ownership changed | Let the current owner continue or use an authorized handover. |
| `canonical_acquire_readback_required` | History is known, current proof is unavailable | Restore the provider and retry the same operation. |
| Acceptance/source rejection | Current control-plane authority changed | Resolve that boundary before attempting work. |

A successful readback is still a point-in-time proof, not a lock over subsequent
external effects. Execution must retain its existing mutation fences; this
change does not close the remaining external-effect fencing work.

Canonical commands recheck their receipt after reading the decision head.
This handles a peer committing the same operation between the first absent
receipt and the head read: recovery precedes duplicate-ID, stale-revision,
Monitor-generation and other new-admission checks. The second read does not
lock the head; commits after it still resolve through CAS and receipt recovery.
Archive preview remains a current-state preview with no receipt lookup.
No provider becomes the default and no legacy writer is re-enabled by this change.

The canonical-only acquire and lifecycle requests are closed and versioned. Joint claim
transfer uses `loopx_canonical_task_lease_claim_transfer_request_v0`, so an older
runtime cannot silently perform only its lease half. The prior
renew-only wire remains accepted for renewal only. An older runtime rejects the
new schema entirely. Missing/invalid fences, changed registration facts before
acquire/renew/transfer, unavailable providers and CAS conflicts fail closed. They never
fall back to a lease file or stale/malformed Markdown. Release admission uses the existing proof without a registration snapshot.
The CLI still resolves its runtime root from the registry or explicit override.

The request decoder separates canonical commands from legacy held-fence requests
as a discriminated union. Canonical commands never construct `lock_token`, PID,
terminal-release or shadow-capture fields. The retained legacy executor shares
the lease decision/materializer, while provider transactions own canonical CAS.
Identity diagnostics now follow the field being decoded: a missing or non-string
Todo ID reports `invalid_todo_id`, instead of being mislabeled `invalid_goal_id`
when the validation message used an underscore.

Responses expose provider/revision/cursor and current-versus-expected version
on a version conflict. They do not invent a `lease_path`, write a second shadow
authority or require Todo Markdown regeneration for a lease-only change.

## Provider and delivery boundary

The local opening handle supplies provider provenance; lease commands no longer
classify stores with concrete File/SQLite class checks. PostgreSQL uses that
same handle only when a service owner supplies the existing scoped factory and
matching store incarnation. There is no credential-bearing CLI option, implicit
service activation or fallback. The real PostgreSQL rehearsal exercises this
public native lifecycle route as well as the storage contract.

This closes standalone acquisition/takeover and existing-lease mutation under
shared-authority L3 / roadmap R5. Real CLI validation carries newly acquired
proof into canonical Todo completion and released readback. Complete/supersede
can use canonical state with a missing Markdown display, then rebuild it through
the existing projection outbox; legacy source requirements remain unchanged.
Executor holder/terminal locks across external effects and automatic cross-agent
result return retain their own callers and qualification. A lease
transfer alone does not prove a completed collaboration journey. No storage
format, default profile, active-Goal migration, D2 soak or D3 promotion changes.

Rollback retains canonical state, receipts and the writer fence. Older code may
reject acquire/transfer/release or the new wire; plan for current lease expiry and
restore compatible code. Do not remove the fence or revive stale lease files.

## Validation

The shared production-scale fixture covers fresh execution, takeover and both lease-only
and claimed-work handover while
retaining its mixed status, decision and historical-lease population. Every
AuthorityStore conformance arm covers native/imported records, negative
admission, a live scope holder beyond display limits, stale senders, response loss, CAS competition, no-op sealing and
historical replay. Real File/SQLite CLI and killed-process tests cover the host
boundary; PostgreSQL uses an isolated real server. NoKV coverage uses its
existing test transport and is not service qualification.

For a read-only Goal snapshot, compare an immutable clean legacy checkout with
File, SQLite and real PostgreSQL through the native public lifecycle entrypoint:

```bash
# LOOPX_TEST_POSTGRES_URL must identify a disposable server.
uv run --extra test python examples/control_plane/authority-lease-lifecycle-rehearsal.py \
  --registry registry.json --goal-id example-goal \
  --baseline-repo ../loopx-baseline --execute-isolated-postgresql
```

Use the source-checkout Python environment and a qualified SQLite Node runtime.
The runner adds three synthetic Todos and two initial leases only to disposable copies, compares
all operation results and non-target records, and verifies that the live source
is unchanged. It separately reports the legacy create-CAS retry mismatch, maintenance
historical-replay rejection and unsupported joint transfer as semantic improvements,
not normalized parity. The native arms prove old-owner rejection and recipient update.
Optional `--private-diagnostics`
keeps raw failures in an owner-only file that must not be published. This does
not replace [D2 capacity and continuity qualification](sqlite-authority-store.md).

## 中文操作与语义

已 promoted 的 File/SQLite Goal，其 acquire、renew、transfer、release 现在使用
同一 provider opening/source fence 与 TS 规则；新领取和失效持有者接管不再进入
旧文件 writer。旧 wire 与未 promoted 路径保留，选择 provider 不构成 promotion。

新执行按上面的 acquire 命令领取；没有旧 lease 时 expected-version 为 0，否则
用 inspect 的当前版本及新的 execution key。领取/接管同时增加 version 和 epoch，
续约只增 version，转交同时增二者并换 key；释放保留 generation。默认转交不改变 Todo
claim；两种转交都不覆盖 exclusion 或扩大 scope。释放只凭匹配 proof，允许到期或注销 owner
清理。所有需递增的入口都拒绝安全整数耗尽，仍允许释放。

已认领且持有有效租约的工作，可显式使用上面的 `--transfer-claim`，在同一 CAS 内
转交 Todo claim 与 lease。仅限 canonical `hard_lease`、open/active Agent Todo，
源 claim 必须属于当前持有者，执行 key/version 精确匹配；双方都必须满足注册、排除
和绑定规则。固定 `bound_agent` 不会被偷偷改写。未认领任务继续使用原 lease-only
操作，旧 writer 拒绝联合交接，不模拟两次独立写入。

联合提交保留 Todo ID、依赖、要求、证据和 scope；旧 continuation note 保留原文，
但 claim 改变后其 facts 校验失效。相同 Agent 换新 execution key 只推进租约，不伪造
claim 修改。旧执行者不能继续更新/完成，新执行者沿用普通 proof-bearing 命令。
回执中的 `claimed_by` 是历史交接结果；重放不会恢复旧归属。开始新操作前 inspect，
丢响应重试则沿用原参数及原 version。digest 绑定显式选项、接收者/key 和 TTL，不能
在重试时删除选项或改目标。

CLI 联合提交后通过原投影器更新展示；失败返回 `projection_delivery=pending`、
`retry_business_mutation=false`。修复展示后执行 `todo project-markdown` 或重试原
交接，只渲染当前 head。需转回时由现任持有者凭当前版本和新 key 发起新的交接，
不能恢复旧文件。该能力不包含上下文自动送达、接收方确认、capability 授权或外部
effect fencing。新的专用 wire 保证旧 runtime 不会只执行 lease 半边；解码后的
判别联合也让 canonical 命令不再携带旧式 lock token、PID、terminal release 字段。
身份错误码直接对应正在解码的字段：缺失或非字符串 Todo ID 现在返回
`invalid_todo_id`，不再因错误文案中的下划线而误报为 `invalid_goal_id`。

完整 canonical Todo/lease 集合决定 scope 冲突，不能只看 UI 页面。归档、排除、
注销或与当前 claim 冲突的 holder 不阻挡新的合格执行。独立 acquire 和原子的
Todo claim + lease 共用状态解释、准入和 materializer；前者使用请求 scopes，后者
仍使用 Todo required scopes。scope 是执行冲突声明，不扩张权限。

一笔 CAS 保存 lease/event/原 receipt。维护操作的身份与既有 renew digest 保持
兼容，历史 replay 可跨后续修改，但不授予当前执行权。Acquire 的身份绑定
Goal/Todo/owner/key，digest 另绑定原 expected version、TTL、scope set；创建时
expected-version 0 的原样重试可恢复回执，改变参数会拒绝。

Acquire 的成功还必须核对当前有效 owner/key/epoch 和资格。同一执行续约后，
重试返回 `lease` 中的当前版本/到期时间，以及 `original_receipt` 中不可变的原始
决定；`current_provider_revision/current_cursor` 标识当前读回。已转交、到期或释放
的旧执行不能凭 receipt 复活。携带 lease 请求的原子 Todo claim 也共享这项检查，
包括首次提交、丢响应恢复、历史重放和只保存 receipt 的 no-op。普通 claim 和维护
receipt 仍是历史语义，不授予新执行权。当前 head 不可读时返回 ambiguous，要求
沿用原 operation id 恢复；不能把原 receipt 中的 active lease 当作当前证明。

各 canonical 命令在读 head 后再次查原 receipt，解决另一调用恰在第一次查无回执后
提交成功的竞争。回执优先于新一轮的重复 ID、陈旧 revision 和 Monitor generation
校验；之后仍由 CAS 防止覆盖并发更新。归档 dry-run 继续只看当前状态，不重放历史。
公开参数、provider 默认值和 legacy 路径保持不变。

canonical acquire 与 lifecycle 各有封闭 wire，旧 renew wire 只接受 renew；旧
runtime 不识别新 acquire schema。fence、provider、注册源变化和 CAS 错误不回退
旧文件。lease-only 命令不生成第二份 shadow 或重写 Markdown。真实 CLI 验证包含
新领取→续约→释放→新执行→完成；complete/supersede 可在 Markdown 展示丢失时
读取 canonical state，完成后经原 projection outbox 重建，旧路径仍要求源文件。

PostgreSQL 使用已有 service-owned scoped factory 和 incarnation 检查，无新凭据
参数或自动启用。四臂演练使用相同公共 native 入口；NoKV 仍只经过测试 transport。
L3/R5 的独立领取/接管及维护由此可用，跨外部 effect 的 executor holder/terminal
锁和自动结果返回仍需各自验收。D2 soak、D3、默认 profile、活动 Goal 迁移和跨主机
部署没有改变。回滚保留 canonical state/receipt/fence 并恢复兼容代码，不得复活旧
lease 文件。演练仅修改隔离副本，核对源与无关记录不变，不能代替完整持久性资格。
