# Monitor configuration and observations

`todo update` changes an existing Monitor's configuration. After whole-Goal
promotion it uses the same TS planning/CAS/receipt owner as other supported
Todo updates; it does not read Markdown as authority. Before promotion it
uses the legacy writer and the same typed configuration policy.

```sh
loopx todo update --goal-id demo --todo-id todo_watch --agent-id agent-a \
  --cadence 2h --next-due-at 2030-01-01T02:00:00Z --dry-run
loopx todo update --goal-id demo --todo-id todo_watch --agent-id agent-a \
  --cadence 2h --next-due-at 2030-01-01T02:00:00Z \
  --update-operation-id monitor-config-1
loopx todo list --goal-id demo --todo-id todo_watch
```

The operation ID in the execute example requires a promoted Goal. Repeating
that ID with the same normalized intent returns its original receipt; changed
intent rejects. Use a new operation ID for a later correction or a reversal.
Dry-run validates without consuming a receipt or repairing display.

The configuration fields are `target_key`, `cadence`, `next_due_at`,
`expires_at` and `watch_only`. Omitted fields remain unchanged; blank strings
retain the existing omission behavior. Python API callers can explicitly clear
an individual field with `None`, for example replace `watch_only` with a valid
`expires_at` in the same update. A Monitor must retain an expiry, a resume
condition or `watch_only=true`. Clearing its final bound is rejected atomically.
Boolean `watch_only` values and their string spellings share a normalized intent.
Changing cadence computes the next due time from the edit timestamp unless
`next_due_at` is supplied explicitly, preserving the legacy schedule contract.
An expiry-only edit leaves the existing due time unchanged.

Configuration does not fabricate `result_hash`, `last_checked_at`,
`monitor_effect_id`, no-change counts or material-change generations. These
belong to the observation lifecycle (`quota monitor-poll` / typed
`MonitorPollObservation`), and raw configuration attempts reject. Historical
import/create codecs retain their own source-validation contract; the no-change
replan threshold stays one of those import/create-owned fields rather than a
public configuration knob. Mapping identity is not a schedule field: a Monitor
successor may carry `target_key` as its route identity without becoming a
Monitor, while cadence, due time, expiry and watch-only require
`task_class=continuous_monitor`.

Once a Monitor has observation evidence, its target identity cannot be changed
or cleared by configuration. Create a new independent Monitor for a different
target; do not reuse the former target's generations as new evidence. An
unobserved Monitor may correct its target. Repeating the same target is valid.

Actor, claim, exclusion and lease checks remain mandatory. A leased metadata
edit requires its current active lease key/version via the existing
`--task-lease-idempotency-key` and `--task-lease-expected-version` options; it
neither renews nor releases the lease. Released/expired history grants nothing.
Reviewed Chat pause/resume/configuration edits use the same admission and
transaction, including registry lifecycle grants when applicable. The reason is
required evidence for grants that demand it, never a grant by itself. Preview
runs the actual dry-run and binds canonical revision/registration. On response
loss or pending display, use **Retry original operation** on the retained card;
reloading the workspace preserves this recovery entry. The cadence is intent;
TS derives its due date only for a new commit. Retry does not reschedule it.
Chat supplies no implicit lease proof, so leased edits keep their existing
rejections. Stop/completion and run-now remain separate lifecycle/runtime paths.

Chat **Run now** keeps the registered responsible Agent separate from its
executable Runtime. An explicit registered actor survives a Runtime switch.
If no actor is selected, choose an existing Goal Agent before confirming the
check. A Goal without an applicable identity can open an Agent binding preview;
after verified binding, the original check receives a fresh, separate preview.
Ordinary Claude Chat retains read-only tools. Confirmed Task channels enable
native execution tools and forward an explicitly selected model/effort. A
run-now receipt proves Turn creation, not monitor completion or periodic
scheduling. See the synthetic [actor preview](images/monitor-runtime-binding/registered-actor.png)
and [binding recovery](images/monitor-runtime-binding/confirmed-binding.png).

Synthetic recovery-card examples: [desktop](../assets/personal-workspace/reviewed-edit-recovery-desktop.png)
and [mobile](../assets/personal-workspace/reviewed-edit-recovery-mobile.png).

A committed configuration can report pending Markdown projection delivery.
Retry its original operation to recover the receipt and deliver the current
projection; do not rerun the change under a new identity to repair display.
Provider selection/defaults and whole-Goal promotion remain unchanged. Reverse
a configuration through a fresh validated update, never by editing a stale
Markdown projection or switching off the writer fence.

## 中文

Monitor 配置修改复用 `todo update`。晋升后由 TS 在同一个 canonical revision 上
校验并提交状态、事件和回执；晋升前保留 legacy writer，复用相同 typed 配置规则。
上面的 CLI 给出了预览、带 operation ID 的执行和回读；operation ID 只适用于已
晋升 Goal，修正或撤销使用新 ID，重试同一请求使用原 ID。

配置字段仅有 target、频率、下次检查时间、到期时间、watch-only。省略／空白保留
旧值，Python API 可用单字段 `None` 明确清除；同一次修改必须保留到期、resume
条件或 watch-only 中至少一项。单改频率沿用旧语义，从修改时间计算下次检查；要保留指定时间，显式传入 next_due_at。
观察 hash、时间、effect ID、无变化次数和变化代数由 observation lifecycle 写入，
普通配置不能伪造；无变化重规划阈值仍属于 create/import 合同，不作为公开配置项。
已有观察证据时不能更换／清除 target，新目标应新建独立 Monitor。target 是路由身份
而非调度字段：Monitor 后继 Todo 可以只带 target 而不成为 Monitor，频率、到期、
检查时间和 watch-only 仍要求 task_class=continuous_monitor。

已有 claim／exclusion／lease 检查继续生效，lease proof 不会因配置而续期。Chat
暂停／恢复／配置编辑复用同一准入和事务，按 registry lifecycle grant 校验委托；
理由只满足 grant 的证据要求，本身不授予权限。预览执行真实 dry-run 并绑定 canonical
revision/注册事实。丢响应或展示 pending 时，在保留的卡片点击“重试原操作”；页面
重载后恢复入口仍可见。频率是意图，TS 仅在新提交时派生 due time，重试不会重新调度。
Chat 不补造 lease proof，带 lease 的编辑保留既有限制；停止／完成及立即运行仍由
各自 lifecycle/runtime 路径负责。

Chat“立即运行”分别保留已注册的责任 Agent 和执行 Runtime。切换 Runtime 不会替换
明确指定的责任身份；未选择身份时，可先选 Goal 的现有 Agent 再确认检查。需要新身份
时先预览并确认绑定，读回验证后重新预览原检查，两个操作分别确认。普通 Claude 对话
继续只读，已确认 Task 才启用原生执行工具并传入选定模型／effort。立即运行回执证明
Turn 已创建，不证明巡检完成或周期调度已启动。
提交成功但展示 pending 时，用原操作重试回执／投影；不能改旧 Markdown 当作回滚。
本切片不改变 provider 默认，不晋升已有 Goal。
