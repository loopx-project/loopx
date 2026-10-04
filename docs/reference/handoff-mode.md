# Goal handoff mode

`soft_claim` uses the Todo assignment; `hard_lease` also requires the existing
execution lease fences. New `set` and migration intents accept these two modes.
`legacy` remains a historical source for backed-up upgrades and original receipt
recovery; new requests cannot select it, even as a no-op. Existing Goals are not
silently changed, and creation/Host defaults remain unchanged in this stage.
This is an ownership policy, not a capability grant or a storage provider.

## Choose the policy independently of storage

Use `soft_claim` for an explicitly single executor without parallel takeover.
Local parallel Agents/automations and shared cloud work need `hard_lease`, with
Host acquire/renew/release and blocked settlement qualified before a default
change. An unknown topology should eventually choose hard, but this command
does not infer topology or silently enable it.

A fresh `coordination-shadow promote` CLI preview defaults to `preserve`: it
changes storage authority while retaining the source policy. Explicit
`--handoff-mode-migration hard_lease` reviews the policy upgrade in the same
fenced cutover. Historical saved v0 hard-only plans retain their original
semantics; saved plans cannot be overridden at execution.
See [reviewed promotion](reviewed-coordination-promotion.md).

## Read or change a quiescent Goal

```bash
loopx --format json handoff-mode show --goal-id example-goal
loopx --format json handoff-mode set --goal-id example-goal --mode soft_claim --dry-run
loopx --format json handoff-mode set --goal-id example-goal --mode soft_claim --operation-id mode-change-1
```

Before promotion, `show`/`set` use the existing frontmatter adapter and locks.
After promotion they use the selected canonical provider, even when Markdown
is stale or absent. Provider failure rejects the operation without fallback;
leftover lease files cannot override canonical records. Only frontmatter and
compact ownership facts cross the legacy TS planning boundary; unrelated body,
metadata, line endings and the final newline remain intact.

A changed `set` requires no unfinished claimed active Todo and no time-active
lease in the complete snapshot. Invalid expiry/schema cannot prove quiescence;
concurrent work invalidates its CAS. Identical soft/hard mode is a no-op even
with active work. Malformed legacy frontmatter can be repaired only when
quiescent; duplicate fields or missing frontmatter reject, and malformed
canonical state is never repaired through the legacy adapter.

Canonical `--operation-id` binds Goal and target. Even an accepted no-op seals a
receipt. Retry with the same ID/target recovers the original decision and never
restores an old mode over later work; changed intent needs a fresh ID.
`ambiguous`/`coordination_receipt_recovery_required` means retry that same request.
Malformed receipts reject. An original `set --mode legacy --operation-id <original-id>`
request still reaches receipt recovery after upgrade, including after a later
policy change. New legacy writes and dry-runs return `handoff_mode_retired`;
`plan-migration` also refuses legacy as a target. The unpromoted writer does not
promise durable operation replay.

## Migrate an assigned canonical Goal with a verified backup

Ordinary `set` is deliberately quiescence-only. To preserve existing assignments
on a canonical Goal, review an immutable plan, then apply its exact digest:

```bash
loopx --format json handoff-mode plan-migration --goal-id example-goal \
  --mode hard_lease --plan reviewed-mode.json
# Inspect source identity/revision/digest, target and preserved claims/leases.
loopx --format json handoff-mode migrate --goal-id example-goal \
  --plan reviewed-mode.json --plan-sha256 <reviewed-digest>
loopx --format json handoff-mode migrate --goal-id example-goal \
  --plan reviewed-mode.json --plan-sha256 <reviewed-digest> --execute
loopx --format json handoff-mode show --goal-id example-goal
```

Plan and preview do not change policy. Apply first exports and independently
verifies `<plan>.backup.jsonl` using the existing portable authority archive.
The archive retains every original transaction, receipt, full projection and
Todo metadata. Goal/runtime, provider identity/revision, projection digest and
registered Agents must match the reviewed source. Drift rejects; use a new plan
path, never edit the digest. A concurrent write during backup fails the exact
CAS and cannot be overwritten. File, SQLite and an admitted service provider
use the same typed rule and receipt owner.

Claims remain assignments. Live claim owners must be registered; retained
active leases must have eligible owners, valid fencing counters and all named
required scopes. Additional standalone lease scopes are retained verbatim;
requirements are not an exact grant template. No scope or lease is invented.
An upgrade to hard retains a valid current proof; a claim without a live lease
must acquire one normally before protected work. A soft migration rejects every
unreleased active lease, including expired ones: expiry alone does not prove a
Host stopped. Settle those executions first.

Apply uses a stable digest-derived operation and the existing durable receipt
recovery. Retry the same plan after a lost response; it returns the original
result without rewriting later work. Missing/corrupt retained backup rejects
recovery. Changing policy back uses a **new** reviewed migration and backup,
not an old archive that would discard subsequent writes. Archive restoration
is a separate, isolated disaster-recovery operation.

The App exposes this same explicit operation in **Goal settings → Task ownership**.
Choose Collaborative claims (`soft_claim`) or Exclusive execution leases
(`hard_lease`), preview preserved assignments/leases, then choose **Back up and
apply**. Preview does not change policy or grant execution. Rejections show
conflict counts and affected Todo identifiers; settle the owning executions
before making a new plan. Unpromoted Goals must first use the promotion workflow.

The App stores the opaque preview handle in browser session storage before
enabling Apply; the server owns the immutable plan and verified backup. After a
lost response or page reload in the same tab, retry the saved preview. Do not
create a new operation to guess whether the first succeeded. A confirmed original
receipt and the separately fetched current policy are distinct: later valid
changes remain authoritative. **Read current policy** retries a failed readback;
**Discard preview and start again** explicitly abandons the browser carrier,
without deleting the server plan or backup. Clearing browser storage loses this
carrier; retain server plans/backups for operator recovery through the CLI.
Disabling browser storage leaves readback available but prevents applying an
unretained preview.

CLI and App share registry validation and the same TS migration owner. Lark
ordinary Todo/lease actions consume that canonical state; policy migration has
no Lark editor in this stage. The capability editor does not own this policy. Migration does not
qualify provider defaults, D1–D3, or PostgreSQL deployment. Complete Host lease
lifecycles and migration of remaining legacy Goals precede legacy execution and
last-caller Python retirement; historical import/receipt readers stay at their
migration/recovery boundary.

## 中文

新增 `set` 和迁移只接受 `soft_claim`、`hard_lease`。legacy 仍可作为升级来源、
历史回执恢复输入，不能再作为新请求的目标；已有 Goal 和创建／Host 默认值不被
静默改写。soft 用于明确的单执行者，hard 用于本机并行 Agent／automation 和云端
共享；默认策略切换前还需验证 Host 的 acquire、renew、release 和阻塞结算。
存储晋升与所有权分开：新 CLI promote 默认 `preserve`，显式 hard 才审核策略升级；
旧 v0 保存计划仍按原合同恢复。

上面的 show/set 命令读取、预览或切换空闲 Goal。晋升后以 canonical provider 为准，
不回退旧 Markdown／lease 文件。改变策略要求完整快照没有未完成的已认领活动 Todo、
没有有效 lease；相同 soft/hard 是 no-op。CAS 防止并发覆盖，canonical 请求用固定
operation ID 恢复原回执，不把后来状态改回去。升级后仍可用原来的
`set --mode legacy --operation-id <原ID>` 确认历史操作；新 legacy 写入、预览和
迁移目标明确拒绝。

有 claim 的 canonical Goal 使用 `plan-migration → migrate 预览 → --execute`。
执行先生成并核验 `<plan>.backup.jsonl`，保存全部事务、原回执、完整历史投影与
Todo metadata；然后在同一个源 revision 上提交 mode。源身份、状态、注册 Agent
或计划变化会拒绝，备份期间的并发写入也不能被覆盖。备份／恢复复用既有 authority
archive，File、SQLite 和获准的 service provider 共用 TS 规则与 receipt owner。

claim 原样保留；有效租约保留原 scope、version 和 epoch，不伪造 lease。
Todo 的必需 scope 是任务要求，不等于本轮 lease 的完整范围；额外 scope 不被删除。
没有有效 lease 的 claim owner 仍须正常 acquire 才能执行受保护写入。soft 迁移拒绝
未 release 的 active lease，即使已过期，因为过期不能证明 Host 已停止。
丢响应后重试同一计划恢复原结果；缺失／损坏备份会拒绝恢复。反向切换使用新计划和
新备份，不恢复会丢掉后续工作数据的旧快照。

App 在 **Goal 设置 → 任务所有权** 复用同一操作：选择协作认领或独占执行租约，
预览保留的归属／租约，再点“备份并应用”。预览不改策略、不授予执行权；冲突展示
数量和相关 Todo，须结清对应执行后重新计划。尚未晋升的 Goal 先完成晋升。

浏览器先保存不透明的预览标识才允许应用；服务端保存不可变计划和备份。丢响应或
同一标签页刷新后重试原预览，不能新建操作猜测旧结果。“原操作已确认”与“当前策略”
分别展示，后续合法变更不被旧回执覆盖。可单独重新读取当前策略；显式放弃预览只
清除浏览器载体，不删除服务端计划／备份。清空浏览器存储会丢失该载体，需保留服务端
计划和备份供 CLI 恢复；禁止浏览器存储时仍可读取，但不能应用无法保存的预览。

CLI 与 App 共用注册信息校验和 TS 迁移 owner；飞书普通操作消费相同 canonical 状态，
本阶段没有飞书策略编辑器，也不新建 capability 设置项。此批不关闭 provider 默认与
D1–D3；先完成 Host 生命周期、
逐 Goal 备份迁移，再删除 legacy 执行分支和无调用方的 Python 业务逻辑。旧格式解析
只保留在迁移／历史回执恢复边界。

## Recover a canonical Todo edit with retained lease history

A canonical Todo can retain a released or expired lease even in `legacy` mode.
That record preserves execution lineage: `todo update` still requires a current
active owner proof. `handoff_mode_requires_lease` does **not** mean that the Goal
has silently switched to `hard_lease`.

Lease-proof rejections of canonical metadata edits now include the actual `handoff_mode` and a
read-only `recovery` projection. It contains no execution key and grants no
permission. The original rejection code and all fences remain unchanged:

| Observation | Recovery |
| --- | --- |
| Active lease held by the current claim owner; missing/stale proof | Inspect the lease and retry using its current proof. Do not acquire a competing execution. |
| Released/expired lease; current owner is eligible and acquisition passes the current mode, acceptance and scope checks | Inspect the version, acquire a short lease with a fresh key, update using the returned proof, then release it. |
| Active foreign holder, divergent claim, or absent claim | Reconcile ownership through its lifecycle; never borrow another holder's proof. |
| `soft_claim`, non-open Todo, acceptance hold or conflicting write scopes | Resolve the reported acquisition blocker. No acquire action is offered. |
| Edit changes retained leased work requirements or status | Use the owning lifecycle transition; acquiring another lease cannot authorize the metadata edit. |

The recovery descriptor uses the standalone `loopx task-lease acquire` command.
Combined `todo claim --task-lease-idempotency-key` is restricted to `hard_lease`
and is not the recovery route for `legacy`. Acquire uses `--owner`, a **fresh**
`--idempotency-key`, `--expected-version` from `task-lease inspect`, a bounded
`--ttl-seconds`, and any projected `--write-scope` values. Retry the original
update with `--task-lease-idempotency-key` and `--task-lease-expected-version`
from the new lease. Release with `task-lease release --owner ...
--idempotency-key ... --expected-version ...` using current owner readback.

An observed version is not a reservation. Concurrent changes can reject the
acquire or update; reread instead of bypassing CAS. Preview and rejected writes
do not acquire/release a lease or publish Todo changes. Recovery does not change
the configured mode, promote a provider, or waive acceptance.

### 保留租约历史时的更新恢复

canonical Todo 在 `legacy` 模式下也可能保留 released／expired lease。它记录的是
执行世代，不能因为过期或已释放就绕过写入 fence。`handoff_mode_requires_lease`
不表示 Goal 已自动切成 `hard_lease`；拒绝结果会返回真实 mode 与只读 `recovery`。

有效的本主租约应 inspect 后使用当前 proof 重试；已释放或过期的租约，只有当前
claim owner 满足相同的 acquire 准入规则时，才提示“inspect version → 用新 key
申请短 lease → 带新 proof 更新 → release”。legacy 必须用独立 `task-lease acquire`，
不能用仅限 hard_lease 的合并式 claim+lease。

异主有效 lease、claim 不一致、soft_claim、不允许执行的 Todo、验收阻塞或 scope
冲突不会得到不可执行的 acquire 建议；修改已租用工作的要求或状态须走对应 lifecycle。
提示不包含 execution key、不授予权限，也不修改 mode 或 provider。并发造成 version
变化时重新读取，不能绕过 CAS；dry-run 和拒绝路径不产生 Todo 或租约写入。
