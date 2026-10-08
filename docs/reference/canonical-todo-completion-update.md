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

The ordinary promoted update facade always reaches the existing typed decoder,
including an empty edit. Empty or whitespace-only `--note` still means omission;
the CLI retains its earlier missing-field check for absent/empty values.
With no other edit, a whitespace-only note or direct empty facade edit reports
`Todo update requires a non-empty patch` on File/SQLite
without importing the legacy Markdown editor, committing a receipt or changing
the record. This replaces the erroneous legacy-writer/fence diagnostic. A valid
retry with the same operation id remains available. Unpromoted Goals retain
their existing empty-edit no-change behavior. No provider or ownership default
changes, and removing this Python routing predicate does not retire the source
writer, capture/recovery readers or permanent Markdown display.

普通 promoted 更新 facade 均交由既有 TS decoder，包括空修改。空白 `--note` 仍表示
省略，不清除已有 note；CLI 对无字段和空字符串保留原参数检查。只有空白 note 或
直接 facade 的空修改在 File/SQLite 返回明确的非空 patch 要求，不加载旧
Markdown editor、不提交回执或修改记录。同一 operation id 仍可用于后续合法重试。
这修正了误入旧 writer/fence 的诊断；未晋升 Goal 的空修改 no-change 行为保持。
provider 与 ownership 默认不变，旧 writer、capture/恢复 reader 和永久 Markdown
投影不因删除该 Python 路由判断而退役。

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

Two unused internal Python policy queries are retired:
`contract.resolve_todo_continuation_policy` and
`mutation_authority.todo_lifecycle_authority_for_goal`. Neither has a production
caller, dynamic registration or persisted operation identity. The current
work-graph course is a teaching consumer; its examples and reading route now
follow the active TS owners rather than the removed queries. Completion policy
selection stays in `completion_policy.ts`; lifecycle grant admission stays in
`todo_lifecycle_decision.ts`. The active Python source adapters retain metadata
normalization, registry fact projection and grant validation. The continuation
enum and persisted values remain readable. Only those two private imports stop
working; package rollback restores them without converting state or rewriting
receipts. This deletion changes no CLI, provider, ownership or upgrade default.

退役两个没有生产调用方、动态注册或持久化操作身份的内部 Python 查询：
`resolve_todo_continuation_policy` 与 `todo_lifecycle_authority_for_goal`。
当前工作图课程仍是教学消费者，本批同步将示例和领读路线迁到活跃 TS owner。
完成策略和生命周期 grant 准入继续由
既有 TS owner 决定；仍活跃的 Python metadata、registry facts 和 grant 校验保留，
continuation 枚举与历史值继续可读。只有这两个私有导入退出支持；回滚代码包即可恢复，
无需转换状态或重写原回执，不改变 CLI、provider、ownership 或升级默认值。

Unpromoted Goals retain their existing Python Markdown adapters. The
shared host validation executor and failure projection replace duplicated
transport plumbing; the TS edit decoder/materializer is no longer owned only
by the ordinary update transaction. Permanent rendering and private command
execution still have real Python callers and are not retirement candidates.

Durable text publication is owned by
`control_plane/runtime/document_io.py`, independently of Todo editing.
`atomic_write_state_text`, `verify_state_text_durable` and
`fsync_state_directory` keep their existing locking precondition, exact UTF-8
newlines, permissions, exclusive create, atomic replace and durability barriers.
Canonical projections and validator declarations, registry/session publication,
supervisor logs, feedback, migration and team-plan adapters use that same Host IO
owner. The old definitions in `todos/active_state_editing.py` are removed;
its live source editing/read helpers remain. Publication or readback failure
still propagates to the caller's existing recovery contract. This changes no
provider, default, format, authority policy or supported legacy upgrade route.

永久文本落盘由 `control_plane/runtime/document_io.py` 负责，解除与 Todo 编辑
模块的依赖。三个原函数保留锁前提、UTF-8 换行字节、权限、排他创建、原子替换和
文件／目录持久化屏障；投影、验证声明、registry/session、supervisor 日志、
feedback、迁移和 team-plan 调用方复用同一 Host IO owner。旧编辑模块只删除这
三个定义，仍活跃的源编辑／读取函数保留。故障仍进入原调用方恢复契约，不切换
provider、默认值、格式或权威策略，也不强制旧 Goal 升级。

Canonical Todo creation, update, completion, supersession and archive do not
import the Markdown line writer or source Todo capture producers during CLI
registration. Bootstrap loads capture producers only for a source-state write;
canonical creation and original-operation recovery do not need them. The source
adapter loads them only when an unpromoted operation needs them. Explicit historic imports
from `loopx.todos` still resolve to the same functions in
`control_plane.todos.line_update`; they do not create another decision owner.
Provider failure still rejects the canonical operation without a source write.

The absence regression runs real File/SQLite new-Goal creation and Todo
lifecycle commands with the line writer and four source Todo capture producer
functions physically removed from a disposable package. Prose write guards and
lease capture evidence remain in their owning adapter and keep their live callers.
Original creation recovery preserves later work when the display is
missing and device defaults change; an unavailable selected provider requires
restoration rather than rebuilding source authority. It qualifies that caller
boundary, not deletion of the supported Markdown writer, backup readers or
receipt recovery. Those paths retain their own last-caller migration exits.
The same absence check covers admitted hard-lease work through material vision
writeback, one quota settlement and exact retry. Missing leases and provider
outages still reject; rebuilding the display does not make it fallback authority.

canonical Todo 创建、更新、完成、替代和归档不再在 CLI 注册时导入 Markdown
行写入器或旧源 Todo capture producer；bootstrap 仅在源状态写入时加载 capture，
canonical 创建和原操作恢复不依赖它。未迁移操作实际需要旧路径时才加载。
`loopx.todos` 的历史显式导入仍指向
同一实现，不新增决策 owner。provider 失效仍拒绝 canonical 写入，不回退到旧源。
缺失模块回归在一次性包中物理移除行写入器及四个旧源 Todo capture 函数，并运行真实 File/SQLite 新 Goal 创建与
Todo 生命周期。显示源缺失且设备默认值改变后，原创建操作恢复仍保留后续工作；所选
provider 不可用时要求恢复，不重建旧源权威。
同一 adapter 中仍有真实调用方的 prose 写入边界和 lease capture evidence 保留。
相同缺失模块检查覆盖 hard-lease 工作的准入、实质 vision 写回、一次额度结算与原
Turn 重试；缺租约和 provider 失效仍拒绝，重建显示不会使它成为回退权威。
这验证调用方隔离；仍受支持的 Markdown writer、备份读取和原回执恢复保留各自的
迁移与最后调用方退役条件。

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

Public creation validates task class, role and User gate scope together through
the existing typed create-authoring plan before provider dispatch. It no longer
makes a separate class-only preflight call. The same plan returns normalized
author and claim identities from one registry snapshot, removing repeated Python
registration reads; the canonical transaction retains its fresh source fence.
The standalone Markdown add codec
retains its class check for callers outside that facade. Priority normalization
still preserves the create request bound by historical operation receipts;
removing a redundant check does not change replay identity or authorize a write.
Requests with several invalid fields still fail; the reported error follows the
complete create and input-validation order.

Caller retirement must also preserve each field's original request behavior:
creation compacts text and resolves priority/binding, preserves note bytes, and
deduplicates capabilities in first-occurrence order. Sorting that list or using
update-note compaction for an original create changes the receipt digest. Real
File/SQLite CLI cases retry independently specified v1 requests after later
canonical edits, checking the original receipt and unchanged newer data. This
is a caller-compatibility gate, not full writer-retirement qualification.

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

公开创建入口在 provider 调用之前，通过现有 TS 创建规划一并检查任务类别、角色与
User gate 范围，移除重复的类别预检调用。独立 Markdown 添加 codec 仍为直接调用方
保留类别检查。完整创建规划从一次注册表快照返回规范化的作者与认领身份，删除
Python 重复注册读取；canonical 事务仍保留自己的新鲜来源检查。优先级规范化继续
保持历史创建回执绑定的请求形态，不改变重放身份或
写入准入。多个字段同时无效时，仍拒绝写入，错误由完整创建检查及输入检查顺序决定。

退役调用方也须保留各字段的原请求行为：创建路径压缩文本、解析优先级与绑定，保留
note 字节，按首次出现顺序对能力去重。能力排序或将更新时的 note 压缩套到历史创建
会改变回执摘要。File/SQLite 真实 CLI 回归先提交独立指定的 v1 请求，在后续权威
修改后重试，核对原回执与新数据保持；这是调用方兼容门，不代表完整 writer 退役。

私有声明先持久保存，权威摘要再引用它；没有被权威 Todo 引用的内容不会成为验证要求。
被选中内容损坏时仍拒绝执行。旧 sidecar 可继续读取，历史创建回执不能回滚新验证器。
此改动不提供私有验证命令的跨主机分发，也不会自动清理未引用内容。

## Completion-state crossing retirement

Completion and terminal update already compose `completion_state.ts` inside
their typed transaction. The following internal crossings have no production,
dynamic-handler or packaged CLI caller after that adoption:

| Retired entry | Last consumer / retained owner |
| --- | --- |
| Python `require_todo_completion_continuation` | No caller; live metadata decoding retains `require_todo_completion_metadata` |
| Python `completion_continuation_for_write` | Facade-only test; completion and update call the TS state owner directly |
| `todo.completion_state.continuation_for_write` RPC and `selectTodoCompletionContinuation` carrier | Retired facade and characterization; `completionContinuationForWrite` remains in the whole TS decisions |

The retired RPC rejects unsupported-method requests. Continuation selection,
contradictory-intent rejection and historical terminal recovery remain covered
through the production owner. Python normalization/cache, import-compatible
enums, original receipt values, Markdown projection, backup readers and live
Host IO remain. This removes neither the Markdown writer nor shadow capture,
and changes no provider default. File/SQLite CLI completion and settlement
recovery are the real-path checks; installation qualification also runs those
paths from a wheel with the retired entries absent. Reverting this code restores
the internal crossing without converting data or rewriting receipts.

完成与 terminal update 已在现有 TS 事务中组合完成状态规则。本批只退役两个无生产
调用方的 Python 入口及其不再使用的 RPC carrier；选择 continuation、拒绝矛盾意图
和历史收尾恢复仍由原 TS owner 承担。Python 编解码/cache、兼容枚举、原回执、
Markdown 投影、备份读取及活跃 Host IO 保留。真实 File/SQLite CLI 和删除旧入口后的
wheel 路径验证完成与结算恢复；本批不删除 Markdown writer/shadow，也不改变默认
provider。代码回滚不需要转换数据或重写回执。

## Monitor source-writer isolation

Canonical Monitor polling also loads no Markdown line writer or source Todo
capture producer. The legacy adapter imports those functions only for a new
unpromoted batch; reading a pre-promotion operation receipt remains available
without them. Monitor decisions, lease proofs and original-operation replay
continue through the existing TypeScript batch and canonical provider owners.

The same real File/SQLite CLI oracle runs with the old functions present and
physically absent: no-change and material-change observations, provider outage
and restoration, replay after lease release, one event and no quota spend or
duplicate successor. Existing interrupted Markdown batch and shadow/outbox
recovery tests remain required. This qualifies another caller boundary, not
removal of the supported writer, historical receipt readers or backup recovery,
and changes no provider default or existing Goal's upgrade requirement.

canonical Monitor poll 同样不加载 Markdown 行写入器或旧源 Todo capture producer。
旧适配器仅在未迁移 Goal 的新 batch 写入时导入这些函数；晋升前原操作回执的读取
仍保留。决策、租约证明和原操作重放继续复用既有 TS batch 与 canonical provider。
相同真实 File/SQLite CLI 检查覆盖旧函数存在／物理缺席两臂、无变化／实质变化、
provider 失效后恢复、释放租约后的重放、一次 event、无额度消耗和重复 successor。
未迁移 Markdown 的原子 batch、shadow/outbox 中断恢复仍须通过原反例。本批只完成
该调用方隔离，不删除仍受支持的 writer、历史回执和备份恢复，不切换默认或强制
已有 Goal 升级。

## Frontier classification retirement

Goal-frontier wait, fallback and replan readers keep their Python imports and
legacy fact codecs. Summary-slot precedence, claimant/exclusion lanes and
diagnostic count floors now belong to the existing TypeScript frontier owner,
using the shared claim-scope rule. The compact internal request carries facts
and row coordinates, not private Todo prose. An explicitly empty executable
view wins over stale display rows; diagnostic count floors never create
selectable work or grant execution. Decimal transport preserves Python integer
observations beyond the JSON number precision boundary. No authority record or
receipt changes. A Goal-context reduction shares its fresh count result with
its replan and final projection helpers, including receipt-bound recovery.
Standalone helpers still read their current summary; there is no cross-call
cache or new public packet field.

File/SQLite quota CLI checks use the same independent oracle before and after
the move. The permanent Markdown narrative projection remains required by
status; canonical storage does not retire that document IO. Existing Markdown
writers, source capture, old backups and original-operation recovery keep
their live callers and separate retirement exits. Code rollback restores the
internal classifier without migrating Goal data or changing provider defaults.

Goal frontier 的等待、fallback 与重规划调用方保留 Python import 和旧输入编码；
summary 视图优先级、认领／排除分组及诊断计数下界交给既有 TS frontier owner，
复用共同 claim-scope 规则。紧凑内部请求只携带事实与行坐标，不传 Todo 私有文案。
明确为空的 executable 视图优先于旧显示行；诊断计数不会制造可执行工作或授予权限。
十进制传输保留超出 JSON number 精度边界的 Python 整数观测值。
同次 Goal context reduction 复用 fresh 计数给重规划及最终投影（含原回执恢复）；
独立 helper 仍读取当前 summary，不新增跨调用缓存或公共 packet 字段。
真实 File/SQLite quota CLI 使用迁移前后同一独立断言。永久 Markdown 叙事投影、
仍有调用方的 writer/capture、旧备份及原操作恢复继续保留。本批不转换数据或回执，
不改变 provider 默认，也不代表全部 Python 或旧 writer 已退役。
