# Agent-assisted upgrade to LoopX 2.0

This migration upgrades an existing LoopX installation and its data **in place**.
Keep the same machine, project, registry, runtime route and Goal identities.
The user's Agent composes the existing upgrade primitives; no transfer service,
new conversation or migration-specific UI is required.

Use an explicitly selected 2.0 release or candidate. A source checkout or
prerelease is not a released upgrade channel. Preview the affected scope and
keep a verified private backup before changing data.

## Give this prompt to your Agent

```text
Upgrade my existing LoopX 1.x installation and work in place to <selected 2.0
version or candidate>. Keep the existing project, registry/runtime routes,
Goals, Agents, complete active and archived Tasks, relationships, materials,
configuration and original operation receipts. Continue my unfinished work
after the upgrade. Work within my existing authorization; ask only for a
necessary choice or permission you cannot resolve yourself.

Inspect the active installation owner/version and each Goal's real authority,
format and ownership policy. Stop affected old writers through their existing
owners, reconcile pending operations, release only authorized leases, and make
a private backup. Upgrade through the existing installation owner, retaining
the rollback copy. Before changing Goal data, verify the backup with a
format-compatible verifier.

Choose only the data operations actually needed: upgrade an old canonical
format; import an unpromoted Markdown Goal into its reviewed canonical provider;
or migrate a historical legacy ownership policy to soft_claim/hard_lease. Keep
valid explicit providers and policies. Preview and verify each existing owner's
backup/plan before applying it. Do not copy the project elsewhere, create an
empty replacement Goal, remap working paths, hand-edit registry/SQL, or invent
execution authority to bypass a refusal.

Read back the same Goals and full Task/configuration data, restart affected
processes on the new version, and continue one useful step of the original
task using its current admission and claim/lease rules. Verify its actual
result. After interruption, recover the original operation and digest; do not
repeat an effect or overwrite later writes. Report the selected versions,
preserved routes/data, verified result, remaining holds and rollback boundary.
If an upgrade primitive fails, preserve the source and name that specific gap.
```

## Reuse the owner for the observed state

Inspect the actual installation's `--help` before executing commands. These
operations have different owners; a successful package update does not prove
that existing Goal data have been upgraded.

| Observed state | Existing entry | Required readback |
| --- | --- | --- |
| Installation or Host material needs updating | `update check/plan/apply`, or the package-manager command reported by the plan | Correct package/runtime and refreshed Host material; retain existing state routes. |
| Existing canonical authority has an old format | `authority-archive upgrade`, then its reviewed `--execute` | Verified backup, current format and preserved history/receipts; no provider switch. |
| Existing Goal still uses Markdown authority | `coordination-shadow inspect-source`, `prepare-import`, `apply-import` | Full source records and settled history imported into the reviewed provider; original paths remain. Recover the same import with `recover-import`. |
| Canonical Goal retains historical `legacy` policy | `handoff-mode plan-migration`, `handoff-mode migrate` | Verified backup, preserved assignments and an explicit supported policy. Recover the same plan after a lost response. |

An expired lease is not proof that its writer stopped. `--writers-stopped`
attests a fact already established through the owning Host; it does not stop
one. An already current canonical Goal needs no import. Setting new-Goal
defaults does not upgrade existing Goals. Storage and ownership policy remain
independent choices.

Reinstalling an old binary does not reverse a data upgrade or clear writer
fences. After new writes, use the owning recovery/export or reverse-migration
operation that preserves them; never unpack an old backup over live state.

See [installation and activation](installing-loopx.md#verify-the-active-layers),
[authority formats](../reference/authority-archive.md),
[existing Goal import](../reference/local-authority-provider-selection.md),
[ownership policy](../reference/handoff-mode.md) and
[backup verification](../reference/configuration-backup.md).

## 可直接交给 Agent 的中文提示

```text
请将我现有的 LoopX 1.x 原地迁移升级到 <指定的 2.0 版本或候选>，升级后继续原来
未完成的工作。保持同一机器、项目、registry/runtime 路由和 Goal/Agent 身份，保留
完整活动与归档任务、关系、材料、配置及原 operation receipts。

在现有授权内自行识别安装归属、版本和各 Goal 的真实 authority/格式/所有权策略；
通过原 owner 停止受影响的旧写方、对账未完成操作并结清获准处理的租约，先私有备份，
再通过安装 owner 更新并保留回退副本；执行数据迁移前用兼容版本校验备份。
只询问无法自行解决的必要选择或权限。

按实际状态复用已有原子入口：旧 canonical 格式升级、旧 Markdown Goal 原地导入
canonical provider、历史 legacy policy 审核迁到 soft_claim/hard_lease。有效的显式
provider/policy 保持不变，逐项预览并核对备份与计划后执行。不搬项目、不改工作路径、
不新建空 Goal 冒充升级，不手改 registry/SQL 或伪造执行权绕过拒绝。

读回原 Goal、完整任务和有效配置，重启需要激活新版的进程，按当前准入和 claim/lease
规则继续原任务的一步并检查真实结果。中断后用原 operation/digest 恢复，不重复副作用，
不以旧备份覆盖后写。最后报告版本、保留的数据与路由、验证结果、剩余阻塞和回退边界。
原子升级入口有问题时保留原数据，只指出并修补具体缺口，不扩建迁移系统。
```
