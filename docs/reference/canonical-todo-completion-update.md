# Canonical User Todo completion updates

A promoted local Goal routes `todo update --status done` for **User Todos**
through the TypeScript terminal transaction. File and SQLite use the same
semantic owner; PostgreSQL exercises it through the service-owned provider.
This does not select a default provider or promote an existing Goal.

```sh
loopx todo update --goal-id example --todo-id todo_observation \
  --agent-id agent-a --status done --note 'Observed outcome verified' \
  --no-follow-up --update-operation-id observation-completion
```

Use the same operation id and intent after a lost response. A new annotation
uses a new id (omitting the id generates one). `--dry-run` validates and previews
without running declared validation, writing a receipt, or delivering a display.
Agent completion continues to require `loopx todo complete`.

## Revising an open Todo validator

A promoted Goal may replace an open, active Todo's declared completion
validator without recreating the Todo. Read the current provider revision, then
send the replacement as a dedicated reviewed edit:

```sh
loopx todo update --goal-id example --todo-id todo_observation \
  --agent-id agent-a \
  --validation-command-json '["python3","-m","pytest","-q","tests/new_test.py"]' \
  --validation-label 'focused validation' \
  --update-operation-id revise-validator-1 \
  --update-expected-provider-revision file:42
```

The TypeScript transaction compares the current declaration digest, commits the
new digest, monotonic revision and public-safe audit receipt under one provider
CAS, and rejects terminal, archived or stale edits. The Python boundary durably prepares digest-addressed private command content
before the provider can reference it. Canonical readback selects that exact
digest; a lost response does not leave projection waiting for a sidecar. Reuse the same operation id, expected revision and replacement after
a lost response; a different intent requires a new operation id and a fresh
read. Validator replacement cannot be combined with another Todo edit.

Completion receipts for a revised validator bind the current declaration
digest. A receipt issued for the previous command, or an unbound legacy
receipt, cannot satisfy the replacement. CLI and managed Turn use the same
facade. The Dashboard Todo details show the current revision, digest and last
actor; it is readback only, so no second editor or Lark-specific authority is
introduced.

## 修改开放 Todo 的验证器

已晋升 Goal 可以在不重建 Todo 的前提下替换开放且仍 active 的完成验证器。调用方先
读取当前 provider revision，再把新命令作为独立的 reviewed edit 提交。TypeScript
事务在同一次 provider CAS 中核对旧声明摘要，并提交新摘要、单调递增的 revision 和
公开安全的审计回执；已完成、已归档或基于旧 revision 的修改会被拒绝。Python 边界
在 provider 提交前持久保存按摘要寻址的私有命令声明；权威读回只选择匹配的摘要，
因此丢失响应不再阻塞投影。丢失响应时复用相同的
operation id、expected revision 和替换内容；新的意图必须使用新的 operation id 并
重新读取。验证器修改不能和其他 Todo 编辑合并提交。

修改后的完成回执必须绑定当前声明摘要，因此旧命令产生的回执或未绑定摘要的历史
回执都不能完成新验证器。CLI 与 managed Turn 复用同一 facade；Dashboard 的 Todo
详情只读展示 revision、digest 与最后修改者，不新增第二套编辑权威或 Lark 专用状态。

## Reading pre-revision Todo heads after upgrade

The v0 Todo read-model manifest predating validator revision history remains
readable. Compatibility recognizes the exact earlier field list for both native
and canonical records; it does not accept arbitrary subsets or a partial
validator extension. A historical manifest cannot contain undeclared validator
revision fields. Record identity, count, content digest and Todo semantics are
still checked.

Readback does not rewrite the stored head or historical receipts. The next
ordinary admitted mutation writes the current manifest through the existing
provider transaction. CLI status, packaged Chat and other projection consumers
share this reader; no provider switch, automatic promotion or new grant occurs.

升级后仍可读取增加验证器修订历史之前的 v0 Todo 记录。兼容只接受 native 与
canonical 各自精确的历史字段清单，不接受任意子集或只增加一半的新字段；历史
清单也不能夹带未声明的修订字段。身份、数量、内容摘要与 Todo 语义仍须通过校验。
读取不重写旧 head 或历史回执，下一次正常获准的更新通过现有 provider 事务写入
当前清单。CLI、打包 Chat 和其他投影入口共享该读取规则，不切换 provider、自动
晋升或扩大授权。

## One edit, one terminal transaction

The update decoder, authoring planner and record materializer are shared with
ordinary edits. The terminal owner checks the **original** Todo's completion
authority and the edited record's update authority; clearing a claim or binding
cannot turn an update-only grant into a completion grant. Existing lease
ownership/requirement restrictions remain: an annotation cannot rewrite an
execution grant. Supplied lease proof must identify a current execution;
expired explicit proof does not enable automatic reacquisition.

The transaction checks linked successors before issuing validation effects.
Self-links and missing successors fail without running a caller command. This
ordering also applies to the existing complete/supersede transaction. Completion
does not invent a decision outcome: use the explicit decision workflow when an
approval, rejection or cancellation must be recorded.

A declared validation command runs in the host after TS admission. The resumed
update binds the issued provider revision, retains the registry source witness,
and refreshes the clock. Any intervening provider commit rejects that resume,
even an unrelated Todo edit. Retry re-reads and revalidates; validation itself
may have external effects. Registry witnessing is optimistic, not an atomic
cross-resource authorization transaction or an executor-held effect fence.

Todo fields, completion state, lease release, projection intent and the business
receipt commit together. Release preserves the existing lease version and epoch.
Markdown is a display projection and is not required to admit a completion.
Private validation declarations remain in their private store; they are neither
imported from an untrusted display nor embedded in public completion receipts.

## Validation timeout and cancellation cleanup / 验证超时与取消清理

The shared host validation executor runs non-interactive declared commands in
an owned POSIX process group. A timeout or caller cancellation force-stops that
group, including children holding inherited output pipes after the leader has
exited. Zero-grace cleanup sends KILL directly; it does not add a termination
delay or replace the original timeout with a TERM/KILL race error. Existing
argv/text parsing, cwd, inherited environment/stdin and privacy-safe receipt
fields are unchanged. Commands that intentionally escape the process group are
not contained by this transport. Windows retains the existing `taskkill /T`
adapter; this is not a new Windows containment qualification.

This is Python OS I/O reuse, not another decision owner: TS still admits the
command, owns the declaration/deadline, checks the source/lease witnesses and
decides completion. A timeout keeps the Todo open and its canonical revision and
private declaration unchanged. It is not a passed validator, accepted progress
or a debit authorization. CLI/managed Turn and existing Chat/Lark consumers keep
the same failure projection; no setting, field editor or separate UI authority
is added. Synthetic File/SQLite real-CLI regression tests verify this readback;
they do not certify a long-running batch fits the synchronous deadline.

共享 host 验证器将非交互式声明命令放入其拥有的 POSIX 进程组。超时或调用方取消会
直接终止整组，包括父进程已退出、仍持有输出管道的子进程；零宽限直接发送 KILL，
不增加延迟，也不让 TERM/KILL 退出竞态遮盖原超时。argv/文本解析、工作目录、继承
的环境与 stdin，以及隐私安全回执字段保持不变。主动脱离进程组的命令不在此传输
的隔离保证内；Windows 复用已有 `taskkill /T`，本改动不宣称完成新的 Windows 验收。

Python 只复用 OS I/O；准入、声明、期限、源/租约见证和完成决策仍由 TS 权威负责。
超时保持 Todo 开放，canonical revision 和私有声明不变，不等于验收通过、进展
获准或扣额授权。CLI/managed Turn 与现有 Chat/Lark 消费方保持原失败投影，不新增
设置、字段编辑器或 UI 权威。File/SQLite 的隔离真实 CLI 回归验证了读回，但不能
证明长批次已经满足同步期限。

## Linked User completion effects

An admitted `todo complete --decision-outcome approve|reject|cancel` now commits
its exact linked Agent Todo effects in the **same** provider transaction as the
User completion and receipt. Ordinary User actions completed through update or
Chat use the same rule. `todos/user_completion.ts` owns this decision for both
canonical providers and the legacy Markdown adapter; Python retains locked
snapshot extraction and writeback, not a second scope/resume implementation.

| Decision / current target | Effect |
| --- | --- |
| Approve a linked gate | Consume only covered required scopes and their recorded negative outcomes; preserve independent requirements. |
| Reject or cancel a linked gate | Keep requirements, replace the latest outcome for that exact scope, preserve independent outcomes, and block the active target. |
| Complete a linked User action | Attempt resume without consuming decision authority. |
| Cancel a linked User action | Close only the source reminder; do not edit or resume its target. |
| Another active linked User Todo, remaining requirement or negative outcome | Keep the blocked target blocked. |
| Explicit blocker task | Require explicit blocker repair. |
| Completed, deferred or archived target | Do not change or reactivate it. |

Approval can consume a requirement on an already open target. Resume preserves
its claim; it does not acquire or transfer the **Agent target's** lease. The
existing exact-gate auto-acquire/release contract still applies to the completing
User gate itself. Supersede never runs approval effects. An unrelated Todo or
unlinked standing approval is outside this exact-target mutation.

This fixes canonical completion previously leaving an approved target stranded.
It also intentionally tightens **both** legacy and canonical behavior: partial
approval cannot resume work with unmet requirements, and late rejection cannot
revive terminal/deferred work. Normal completion without a linked target is
unchanged. No provider selector or capability default changes.

`unblock_resume` and `decision_scope_resolution` are historical business results,
not fresh permission. Existing receipt identities remain valid. Replaying a
pre-fix receipt does not retrofit missing effects; an already completed gate is
not a new owner decision. Reconcile such an inconsistent target against the
recorded decision through an explicit reviewed repair. Never reopen a gate just
to obtain another approval, or change an operation id to reinterpret old intent.

Concurrency rejection writes neither the User completion nor its dependent
effects. After a lost response, recover the same receipt; after display loss,
rebuild from the current canonical head. The packaged Chat HTTP tests cover
linked User-action completion, failed validation, readback and proposal retry.
The existing decision-gate UI directs explicit decisions to the CLI, so no new
frontend control or Lark permission surface is introduced.

关联 User 完成与目标 Agent Todo 的决策消解、阻塞状态、原操作回执在同一事务内提交。
旧 Markdown 路径也调用同一个 TS 规则；Python 仅保留锁内快照适配与写回。批准只消解
覆盖的要求，拒绝/取消保留要求并记录结果；普通 User action 不消费授权。其他关联
User Todo、剩余要求或拒绝结果仍会阻止恢复；已完成、延期、归档任务不会被晚到决定
重新激活。上述两项安全修复同时影响旧路径和 canonical 路径，其他默认值不变。
重放返回历史回执，不补做旧版本遗漏的联动，也不产生新的授权；历史不一致须根据
原决定显式核对修复。目标任务的执行租约与用户批准仍是不同合同。

## Closing an ordinary bound User action / 关闭普通绑定用户事项

For a promoted Goal in `hard_lease` mode, the exact registered bound Agent may
close an ordinary `user_action` without acquiring an execution lease. User
actions cannot acquire execution leases; this is an administrative terminal
edit under the existing provider CAS, not an execution grant. A foreign actor,
an excluded/unregistered actor, an active lease holder or stale explicit lease
proof is not exempted. No claim, lease generation or decision scope is created.

```sh
loopx todo complete --goal-id example --todo-id todo_observation \
  --role user --agent-id agent-a --decision-outcome cancel \
  --evidence 'The observation request was withdrawn'
```

Only `cancel` is accepted as an explicit ordinary-action outcome. A linked
reminder reports `decision_cancelled` and leaves its Agent Todo, requirements and scope
outcomes unchanged. Ordinary completion with no decision outcome retains the
existing guarded resume behavior. Gate approval/rejection/cancellation retains
its explicit gate contract. Cancelling a reminder is not cancelling an order,
withdrawing an external message or authorizing a trade; expiry is not detected
or acted on automatically by this change.

Compatibility: canonical `todo complete` continues to accept a `user_gate`
without `--decision-outcome` as closure only, not approval, rejection or
cancellation. Its required scopes, scope outcomes and blocked dependents remain
unchanged, just as for `todo update --status done`. To record a decision and its
linked effects, explicitly supply `approve|reject|cancel`. The legacy Markdown
explicit-completion adapter keeps its pre-existing requirement for a decision;
that adapter's stricter input rule is not imposed on native callers. Historical
successful receipts remain replayable as recorded.

The delivered cancellation entry point is CLI/managed CLI. Existing Chat
completion continues through the shared terminal owner, and frontend/Lark
consumers read the canonical completed status; no new cancellation button,
configuration setting or chat-specific authority is introduced. Direct
frontend/Lark cancellation controls are not part of this bounded slice. The
canonical transaction is qualified on File, SQLite and real PostgreSQL. The
legacy Markdown adapter shares outcome validation and cancellation effects,
but its separate hard-lease terminal fence is not changed. Historical receipts
are replayed as recorded, not reinterpreted as a new cancellation.

已晋升且启用 `hard_lease` 的 Goal 中，精确绑定、已注册且未被排除的 Agent 可以
关闭普通 `user_action`，无需取得执行租约。用户事项本来不能领取执行租约；这里是
既有 provider CAS 下的行政关闭，不产生认领、租约代次或批准权限。异主体、活跃
租约及显式过期/错误租约证明仍不绕过检查。

普通事项仅接受显式 `cancel`：关联提醒报告 `decision_cancelled`，不修改或恢复关联 Agent
任务，不消解要求或写入决策范围结果。不带决定的普通完成保留既有受保护恢复行为；
用户 gate 的批准、拒绝和取消仍遵循原契约。取消提醒不等于撤单、撤回外部消息或
交易授权，本改动也不自动检测到期。

保持兼容：canonical `todo complete` 继续接受未带 `--decision-outcome` 的
`user_gate`，仅关闭事项，不视为批准、拒绝或取消决定；关联任务的阻塞、范围要求
和范围结果保持不变，与 `todo update --status done` 一样。如需记录决定及关联
效果，须显式传入 `approve|reject|cancel`。旧 Markdown 显式完成适配器保留原有的
决定必填规则，不将该适配器更严格的输入规则强加给原生调用方。历史成功回执仍
按原记录重放。

本交付的取消入口是 CLI/managed CLI；既有 Chat 完成入口复用同一终结权威，前端和
Lark 读取 canonical 完成状态，但不新增取消按钮、配置或独立聊天权威。直接前端/
Lark 取消控件不属于本有界切片。File、SQLite 和真实 PostgreSQL 已验证该事务；旧
Markdown 路径复用结果校验和取消联动，但不改变其独立 hard-lease 终结门禁。历史
回执按原记录重放，不会被重新解释成新的取消。

## Recovery and callers

- Historical replay precedes current source admission and returns the original
  business receipt. Changed edit intent with the same operation id is rejected.
- An already completed User Todo can receive a new completion-update annotation
  without rerunning validation or changing its completion timestamp. This does
  not reopen the Todo or grant further execution authority.
- The existing Chat User-completion action uses its reviewed canonical revision
  and proposal operation id. Failed validation produces a failed proposal with
  no success receipt. Pending display delivery is retryable; a retry recovers
  the canonical operation before checking present-day freshness, then projects
  the current head. Agent completion keeps its existing dedicated route.
- CLI and Python use the same public facade. No frontend layout, action name,
  status selector or provider setting is added. The shared typed review plan
  now exposes the original-operation retry for canonical User completion too.
  The existing completion button
  and failed-proposal retry interaction remain the user entry points.

The same synthetic pending-completion proposal on the packaged desktop surface:
[before: omitted from recoverable proposals](images/canonical-user-completion/before.png),
[after: the original-operation retry](images/canonical-user-completion/after.png).
These images use the repository's synthetic workspace fixture, not a live Goal.
No responsive layout changes are involved.

The local update transport uses request v3 for the completion envelope. v0–v2
reject that envelope instead of silently accepting only part of the intent.
Their ordinary update behavior and receipt encoding remain unchanged. The
provider-neutral terminal receipt includes the User edit identity only for this
new operation family; existing complete/supersede receipt identities remain valid.

## Migration boundary

Unpromoted Goals retain their existing Python Markdown/event adapters. The
shared host validation executor and failure projection replace duplicated
transport plumbing; the TS edit decoder/materializer is no longer owned only
by the ordinary update transaction. Permanent rendering and private command
execution still have real Python callers and are not retirement candidates.

This closes the User completion-update caller within TS T1/T2 and local-default
L2. It does not close every Monitor/event caller, executor-held effect fencing,
D1 consumer recovery, SQLite D2 capacity/elapsed soak, or D3 whole-Goal cutover.
See the [local-default program](../architecture/rfcs/shared-goal-authority-state-provider-v0.md#execution-handoff-and-integration-order).

Rollback the code before using the new caller, or finish/retry its outstanding
projection delivery before downgrading. Older binaries reject request v3 and
cannot recover this operation through the old update route. Existing durable
Todo/lease records, historical receipts and permanent import/export obligations
are not removed by this change; never revive stale Markdown as authority.


## Retrying canonical Todo creation

For an already promoted File/SQLite Goal, provide a stable caller operation id:

```sh
loopx todo add --goal-id example --role agent --claimed-by agent-a \
  --text 'Validate the artifact' --operation-id artifact-create-1 \
  --validation-command-json '["python3","-m","pytest","-q","tests/test_artifact.py"]'
loopx todo receipt --goal-id example --operation-id artifact-create-1
```

Retry the same `todo add` intent with the same id after a lost response. The
TypeScript receipt recovers the original Todo even if its text or validator
has since changed. Changing the intent under the same id is rejected. An
omitted id is generated and returned on success or ambiguous timeout; callers
that must survive process termination should choose the id before dispatch.
Legacy Markdown creation rejects this option instead of pretending to provide
canonical idempotency.

Validation content is prepared privately before create/revision dispatch. Its
presence alone never activates a validator: the authoritative Todo selects its
exact digest. Corrupt selected content fails closed. Legacy per-Todo sidecars
remain readable when no digest-addressed content exists. Rejected requests may
leave unreferenced private content; this change introduces no automatic deletion
of declarations that historical receipts may still reference. An old create
retry cannot replace the current canonical validator. This repairs local
publication recovery, not cross-host distribution of private validation commands.

对已晋升的 File/SQLite Goal，调用方可在 `todo add` 传入稳定的
`--operation-id`。响应丢失后用同一编号和同一意图重试，TS 回执返回原 Todo，
不会因 Todo 后来改名、完成或修订验证器而重复创建。相同编号搭配不同意图会被拒绝。
省略编号时会自动生成并在成功或不确定超时错误中返回；需要应对进程终止的调用方
应在发送前自行确定编号。旧 Markdown 路径不支持此参数。

私有声明先持久保存，权威摘要再引用它；没有被权威 Todo 引用的内容不会成为验证要求。
被选中内容损坏时仍拒绝执行。旧 sidecar 可继续读取，历史创建回执不能回滚新验证器。
此改动不提供私有验证命令的跨主机分发，也不会自动清理未引用内容。
