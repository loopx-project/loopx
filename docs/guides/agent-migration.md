# Let your Agent handle migration

Give the prompt below to the Agent that currently owns the work. Fill in only
its destination; let the Agent discover the source, select the existing tools
and carry out the move. LoopX does not need a separate migration scheduler,
background executor or migration-specific conversation for this workflow.

## Copyable prompt

```text
Move my current Agent work to <destination machine or workspace>, then continue
my unfinished task there. Handle the migration within my existing authorization.
Ask only for a destination, credential login or other necessary choice that you
cannot discover or safely complete yourself.

First inspect the installed LoopX version and its existing commands. Reuse its
backup, verify, restore and supported context-adoption primitives. Do not build
a new migration system or assume that this installation supports every Host.

1. Inventory the original objective, unfinished tasks and relationships, current
   branch and uncommitted files, required materials, configuration and tools.
   Save a private handoff with the next concrete action, acceptance criteria,
   relevant evidence and known failures. Distinguish historical IDs from current
   execution authority; keep the actual files the task needs, not just a summary.
2. Quiesce the source through its existing owners. Reconcile unfinished effects
   and release only leases you own through their normal commands. Keep the source
   and its rollback copy; do not let source and destination execute concurrently.
3. Preview the smallest existing backup/export that covers those dependencies,
   create it in private storage, and verify its manifest and digest. Keep raw
   archives, session history and configuration private. Authenticate the destination
   separately; do not transplant source credentials, timers or live grants.
4. Restore into a new isolated destination and verify the captured files and
   complete task data. Map paths and rebind reviewed configuration through the
   existing owners. Never overwrite an occupied destination, recreate missing
   authority as an empty Goal, or edit registry/SQL to bypass an import refusal.
5. Where supported, use the existing native checkpoint/session admission and
   context-adoption path. Otherwise give the destination Agent the complete
   handoff and files, and say explicitly that this is a fresh conversation.
   Obtain current Goal/Task admission and claim or lease where required; restored
   historical receipts or leases do not authorize execution.
6. Read back the destination task, materials and effective configuration. Perform
   one useful step of the original task and check its real result against the
   original acceptance. Report what moved, what was verified, what still needs
   rebinding and the exact next action. Keep the source stopped until the switch
   is verified. After an ambiguous response, inspect the original operation;
   do not repeat a side effect or debit to discover whether it happened.

If an existing primitive is insufficient, name the specific missing operation
and preserve the source. Propose only that bounded primitive fix; do not add
background continuation, a new authority or a specialized migration UI.
```

## Existing tools to use

Inspect `loopx --help` and each command's `--help` on the actual installation.
The Agent supplies paths, identities and reviewed digests; the user should not
have to assemble the commands manually.

| Need | Existing primitive | Boundary |
| --- | --- | --- |
| Private LoopX state copy | `backup-state`, `backup-state verify` | Inspect included/missing roots; verified extraction remains inert. |
| Configuration copy | `configuration-backup export/verify/restore` | Restore is an isolated checkpoint; apply reviewed values through the existing settings owners. |
| Complete local Todo authority | `authority-archive export/verify/restore` | Retain full canonical records; restoring data does not activate a Goal or grant execution. |
| Supported native history/context | `research-checkpoint capture/verify/admit-session/adopt-context` | Optional, Host-specific path; use its current admission and original operation identity. |
| Current execution | Normal Goal, Task and lease commands | Use fresh destination authority, not a copied session or source lease. |

See [configuration backup](../reference/configuration-backup.md),
[authority archive](../reference/authority-archive.md) and
[explicit research checkpoints](../reference/research-checkpoint.md) for the
owning contracts. A verified backup alone is not a completed migration.

## 可直接交给 Agent 的中文提示

```text
请把我当前 Agent 的工作迁移到 <目标机器或工作区>，并在目标继续原来的未完成任务。
请自行盘点当前目标、任务关系、分支及未提交文件、必要材料、配置和工具，优先复用当前
已安装 LoopX 的备份、校验、隔离恢复和受支持的上下文接续入口，不另造迁移系统。

在现有授权内完成操作；只询问无法自行确定的目标、凭证登录或必要选择。先通过现有
owner 停止源端写入，核对未完成的副作用并正常释放自己的租约，保留源和回退副本。
创建私有备份和迁移交接，写清原任务、验收、证据、已知失败及下一步，携带真实依赖文件。
在全新隔离目标中校验完整数据，映射路径并通过原配置入口重绑；凭证在目标重新登录，
不复制旧租约、执行权、定时器或机器登录。不要覆盖已占用目标、重建空 Goal 冒充恢复，
也不要手改 registry/SQL 绕过拒绝。

支持原生历史接续时调用既有准入和上下文采用能力；不支持时，把完整交接与文件交给
目标 Agent，明确这是新会话。按当前策略取得 Goal/Task 准入及所需 claim/lease，再继续原工作的一小步，
按原验收检查真实结果。最后说明已迁移和已验证的内容、仍需重绑的项及准确下一步。
切换确认前不让两端同时执行；响应不确定时查原 operation，不重复副作用或扣费。

若现有能力缺一项原子操作，请指出具体缺口并保留源，只提出该项小补丁；不要扩展成
后台自动续办、另一套 authority、迁移专用会话或 UI。
```
