# Goal handoff mode

`handoff-mode` chooses the ownership rule used by existing Todo/lease operations:
`legacy` retains the claim/lease compatibility model, `soft_claim` uses the Todo
claim, and `hard_lease` requires the existing lease fences. It is not an Agent
capability grant, provider selector, or Goal promotion command.

## Read and change

```bash
loopx handoff-mode show --goal-id example-goal --format json
loopx handoff-mode set --goal-id example-goal --mode soft_claim --dry-run --format json
loopx handoff-mode set --goal-id example-goal --mode soft_claim --format json
```

Before promotion, these commands use the existing frontmatter writer and its
state/event/lease locks. After promotion, they use the selected canonical provider;
`show` returns `source=canonical_provider` and its `provider_revision`, even if
Markdown is stale or missing. `--runtime-root` applies to both show and set.
Provider errors fail closed. A leftover local lease file cannot override an
empty canonical lease collection.

A mode change requires no unfinished claimed active Todo and no time-active
lease. The canonical transaction checks the complete Todo/lease snapshot,
including records outside display limits. An expiry equal to the observation
time is expired; an invalid active lease timestamp or unknown lease schema
cannot prove quiescence. Concurrent mutations invalidate the CAS snapshot and
return a conflict without switching the mode. Todos, lease records and their
read-model digests are preserved by the mode change.

The unpromoted scan now includes the same complete event-overlay Todo view as
Todo listing, without its display limit. This changes the previous behavior:
an event-only claim now rejects a mode switch. Every configured/fallback event
candidate must be readable; corrupt input returns `handoff_mode_source_unavailable`
instead of silently falling back to apparently empty Markdown. The append store
locks (including absent candidate paths) remain held through the durable mode
write, followed by the existing per-goal lease mutex. Direct unsupported file
edits are outside this contract.

Both paths use the same TS claim/lease classifier and mode-transition rule.
An identical valid mode remains a no-op even with active work. A malformed
legacy mode can be repaired only when quiescent; the result retains its actual
`previous_mode` and `previous_mode_valid=false`. Duplicate mode fields reject
with `handoff_mode_duplicate_field`, and missing frontmatter rejects a changed
mode with `state_frontmatter_missing`. Canonical malformed state still rejects;
legacy repair does not grant permission to repair a canonical head.

Only frontmatter and compact ownership facts enter the legacy TS plan. Python
keeps the source locks, event projection and existing capture/writeback adapter;
the body never crosses the mode-plan transport. The scalar replacement preserves
unrelated metadata, CRLF/LF, Unicode separators and the final newline. No default
mode, provider or capability changes.

## Preserve claims during authority promotion

The reviewed whole-Goal authority cutover has a narrower migration option for
an active Goal that cannot satisfy the ordinary quiescence rule:

```bash
# Keep legacy or soft_claim while changing only the storage authority.
loopx coordination-shadow promote --goal-id example-goal \
  --minimum-operations 3 --require-event-kind todo_claim \
  --handoff-mode-migration preserve

# Move legacy/soft_claim directly to hard_lease in the same reviewed cutover.
loopx coordination-shadow promote --goal-id example-goal \
  --minimum-operations 3 --require-event-kind todo_claim \
  --handoff-mode-migration hard_lease

# Apply only the exact plan returned by preview.
loopx coordination-shadow promote --goal-id example-goal \
  --minimum-operations 3 --require-event-kind todo_claim \
  --handoff-mode-migration hard_lease --execute
```

This is not a general mode-change bypass. The only explicit choices are
`preserve` and `hard_lease`; omitting the option retains the older requirement
that the qualified source already be `hard_lease`. The TypeScript promotion
transaction preserves every Todo, claim, lease record, receipt and validation
field. It validates live claim owners against the Goal agent registry and
retains an active lease only when its owner, Todo scopes, expiry, version and
epoch are safe. It never invents a lease for a preserved claim. After a direct
move to `hard_lease`, the same claim owner must acquire a fresh lease through
the ordinary atomic claim-and-lease path before protected work; another owner
remains rejected.

Preview reports the source revision/digest, target digest, preserved claims,
lease dispositions and conflicts. The target digest, selected migration and
registered-agent set enter the promotion-plan identity. Therefore an
interrupted cutover can recover only the same reviewed intent. The durable
legacy-writer fence blocks late old-session writes after cutover; a zero active
lease count alone is never treated as proof that no old Turn exists.

The CLI is the only mutation surface for this reviewed administrative action.
Managed Turns invoke that same CLI contract. Dashboard delegation preflight and
Lark/Chat remain read-only here: they already project `promotion_required` or
the promoted canonical authority and direct an operator to the reviewed
preview. The migration choice is one-shot operation intent, not Goal
configuration, so adding it to the capability editor would create a second
source of truth. After apply, all ordinary Todo/lease actions and receipts on
those surfaces read the same promoted projection.

## Recover a canonical request

Choose an operation ID before a canonical set if a lost response must be retried:

```bash
loopx handoff-mode set --goal-id example-goal --mode soft_claim --operation-id mode-change-1 --format json
# Repeat this exact intent to recover its original receipt.
loopx handoff-mode set --goal-id example-goal --mode soft_claim --operation-id mode-change-1 --format json
loopx handoff-mode show --goal-id example-goal --format json
```

The ID binds the goal and requested mode. Reuse with a different mode is rejected.
A retry's clock may advance; it still recovers the original result. Even an
accepted unchanged canonical set seals a receipt and advances provider revision,
while returning `changed=false`. If another mode was selected afterward, replay
returns the original decision without restoring it. Use `show` for current mode.
A thrown commit response follows the same durable receipt recovery as other
canonical commands. If the write may have committed but the receipt cannot be
read, the result is `ambiguous` with `coordination_receipt_recovery_required`:
retry the same operation ID. A malformed historical decision is rejected as
`invalid_coordination_command_receipt`, not coerced into an unchanged success.

Preview writes neither a mode nor an operation receipt. `--operation-id` requires
canonical authority; the legacy writer does not promise durable operation replay.

Select a previous mode with a **new** operation ID to change it back, subject to
the same quiescence check. Do not disable the writer fence or restore old Markdown
to roll back a canonical change. The existing Todo-section renderer does not
project frontmatter: canonical mode is read through `handoff-mode show`, not a
possibly old frontmatter value. This command does not qualify a provider profile,
complete D1–D3, deploy PostgreSQL, or authorize active-Goal migration.

## 中文

`handoff-mode` 选择 Todo 的 claim／lease 所有权规则，不授予 capability、不选择
provider，也不执行 Goal 晋升。上面的命令分别用于读取、预览和切换。

晋升前保留 frontmatter 与本地锁兼容路径；晋升后从 canonical provider 读取，
Markdown 缺失／陈旧和遗留本地 lease 不再影响判断。`show` 返回来源及 revision；
provider 失败明确报错，不回退旧文件。现有 Todo-section 投影不包含 frontmatter，
因此当前 mode 应通过 `show` 查询。

切换要求完整快照内不存在未完成的已认领活动 Todo、不存在有效 lease。过期时间
恰好等于观察时间视为已过期；非法有效期或未知 lease schema 不能作为空闲证据。
并发修改使 CAS 冲突，不能在旧检查结果上继续切换。原 Todo、lease 和摘要不变。
未晋升路径现在也读取完整事件覆盖视图，包含显示分页之外的 Todo。因此旧行为发生改变：
仅在事件中存在的 claim 也会阻止切换。所有事件候选源必须可读，损坏源返回
`handoff_mode_source_unavailable`，不能回退 Markdown 后宣称空闲。事件追加锁从读取
保持到模式写回完成，再配合已有 lease 锁；直接手改文件仍不在该合同内。

两条路径共用 TS 所有权分类和切换规则。相同合法模式仍是 no-op；非法旧模式以显式
无效状态进入修复，只允许在无在途工作时修复，不再伪造另一个合法旧模式。
重复字段拒绝为 `handoff_mode_duplicate_field`，缺少 frontmatter 时拒绝变更。
只把 frontmatter 和必要事实传给 TS，Python 保留锁、事件投影和 capture 适配；
正文不进入计划传输，并保留 CRLF、Unicode 分隔符和末尾换行。默认模式和 provider 不变。

canonical 提交响应丢失时复用已有回执恢复；若回执暂时不可读，返回 ambiguous 并要求
以同一个 operation ID 重试。损坏的历史决策明确拒绝，不能当作“成功但没变化”。

对于无法清空活跃 claim 的 Goal，整 Goal authority 晋升提供一个更窄的显式迁移入口：
`--handoff-mode-migration preserve` 只切换存储权威并保留 `legacy`／`soft_claim`；
`--handoff-mode-migration hard_lease` 在同一受评审事务中直接迁到 `hard_lease`。
未传该参数时，继续沿用“源端已经是 `hard_lease`”的旧门禁。它不是通用 mode 绕过，
也不开放降级。

TypeScript 事务会原样保存 Todo、claim、lease record、receipt 与验证字段；校验活跃
claim owner 是否仍在 Goal agent registry 中，并且只有 owner、Todo scope、expiry、
version 与 epoch 都安全时才保留活跃 lease。迁到 `hard_lease` 不会为 claim 伪造
lease：原 owner 下一次受保护写入前，必须走正常的原子 claim+lease 路径取得新 lease，
异主仍被拒绝。preview 会给出源 revision/digest、目标 digest、保留 claim、lease
处置与冲突；这些内容进入 promotion-plan identity，所以中断后只能恢复完全相同的
评审意图。持久 legacy-writer fence 负责拦截旧 Turn 的迟到写入，不能用“当前 0 条
active lease”推断没有在途 Turn。

该受评审管理动作只有 CLI 一个写入口，managed Turn 也调用同一 CLI contract。
Dashboard 的 delegation preflight 与 Lark／Chat 在这里保持只读：它们已经投影
`promotion_required` 或晋升后的 canonical authority，并把 operator 引导到受评审
preview。migration choice 是单次 operation intent，不是 Goal 配置；把它再放进
capability editor 会制造第二个 truth source。apply 之后，各入口的普通 Todo／lease
动作与回执统一读取同一份 promoted projection。

需支持丢响应恢复时，在首次 canonical set 前指定 `--operation-id`，重试沿用同一
目标 mode 和 ID。不同 mode 复用 ID 会被拒绝；即使最初 mode 未变，也记录耐久回执。
若后来已切到其他 mode，旧请求重放只返回原回执，不把 mode 改回去；用 `show` 读当前值。
预览不写入；旧 writer 不支持该幂等 ID。需要切回时，用新 ID 请求原 mode，仍须满足
空闲门禁，不能通过关闭 fence 或恢复旧 Markdown 回滚。本功能不解除 provider
默认值、长程资格化、PostgreSQL 部署或 D1–D3 的剩余条件。

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

The existing `open -> blocked -> open` lifecycle also accepts a typed
prerequisite wait: use `todo update --status blocked --resume-when
todo_done:<dependency-todo> --reason '<bounded cause>'` after the active holder
releases its execution lease. This explicit wait form is newly supported for
native hard-lease Todos; the prior clear-wait pause form is unchanged. A live
lease or bundled execution proof still rejects the transition. Resume with
`--status open --clear-resume-when --reason '<resume basis>'`; neither transition
grants execution authority, and the next execution requires a fresh lease.

既有 `open -> blocked -> open` lifecycle 也支持 typed 前置任务等待：active
holder 先释放执行 lease，再用 `todo update --status blocked --resume-when
todo_done:<dependency-todo> --reason '<bounded cause>'` 暂停。此显式 wait 形式
新增支持 native hard-lease Todo；原 clear-wait pause 形式保持不变。有效 lease
或附带执行 proof 仍被拒绝。用 `--status open --clear-resume-when --reason
'<resume basis>'` 恢复；两次转换都不授予执行权限，下一次执行仍需新 lease。

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
