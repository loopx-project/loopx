# Required work context

Ordinary Heartbeat/quota delivers the full effective selected Todo, enabled
canonical acceptance and Agent-scoped User obligations at
`interaction_contract.agent_channel.work_context`. These are current work
sources, with readback/revision metadata; no TurnEnvelope switch is needed.
The selected Todo's display body is a display-only view and may be bounded.
Its exact detail read is authoritative for the current body and is validated
against the selected identity, lifecycle, claim and source revision. When a
full-body snapshot accompanies the selection, a mismatch remains pending and
requires a fresh guard. When the selected body is byte-for-byte the same as the exact read, `work_context.selected_todo_ref`
reuses it and the source retains its identity, lifecycle, claim, continuation,
relations and authority revision without repeating the body. If no other
canonical fields need a source record, `work_context.selected_todo_authority`
carries the source and revision. When the view is bounded, the full canonical
detail remains in `work_context.sources`.
Registered Goal state remains a full progressive read in
`agent_channel.required_reads`: the mixed Markdown document can also contain
other tasks and historical evidence, so quota checks its availability but does
not automatically copy that whole document into every response or infer Goal
intent by trimming headings. Read it before dependent work. Enabled scoped
acceptance does not replace the original Goal intent.

Already delivered sources disappear from `required_reads`; unresolved provider
reads retain exact commands and ordering. The typed interaction owner owns this
selection and fulfillment policy in both ordinary packets and TurnEnvelope.
`work_context.complete` means no known source failure, not that pending reads
have been consumed. TurnEnvelope signs the same content and obligations; its
size warning never truncates current requirements or removes a pending read.

Preferences remain conditional on scoped records. The existing preference and
Explore hooks return their actual read context through registered adapters;
the generic kernel never executes the displayed shell commands. Discovery
receipts stay separate from the single content carrier. Unconfigured hooks
preserve their off-state. User Todo inventory views disappear only from exact
Todo responses; scoped User decisions and gates remain in the work context
and retain their existing admission/notification authority.

Source failure retains the unresolved read, identifies the unavailable source,
and blocks dependent delivery until recovery and a fresh guard. Reading current
content is not an execution grant or proof of Goal completion.

For an exact Todo, the existing command now returns one complete record:

```sh
loopx --format json todo list --goal-id example-goal --todo-id todo_work
```

Read `todo.text` plus identity, status/claim, `relations`, source and canonical
`authority_read` revision. The former `todos`, `agent_todos` and `user_todos`
views are removed from exact responses. There is no opt-in compatibility flag.
Inventory reads without `--todo-id` retain list views; `--thin` belongs to those
bounded lists and is rejected with an exact identity. A full requirement tail
must never be replaced by a summary. Markdown also renders the original once.

Missing or filtered work returns `matched=false`, `todo=null`, `not_found=true`.
Ambiguous records and source failures fail visibly, without stale-display
fallback. Blocked, completed and archived records remain read-only observations.
An exact read grants no claim, lease, execution, publication or quota authority.
Changed requirements require fresh admission under the original owner.

The existing Python reader retains source resolution and filtering. It passes
one source row to the registered TypeScript `todo.context.page` projection,
without transporting duplicate body or role-summary views. Chat manager detail,
Explore writeback and monitor settlement use the exact record rather than a
removed list alias. No second authority store or selection rule is introduced.

This changes default exact CLI/API output and Heartbeat/Turn context delivery. Regenerate saved expanded heartbeat prompts after installing the
change. Bootstrap prompts load installed rules on the next wake. TurnEnvelope
transport remains an explicit projection choice; this change alone neither
upgrades installed automations nor proves model adoption or token/latency gains.
To roll back, install the prior revision and regenerate prompts; changing a
transport flag does not restore the old exact Todo schema.

## 中文

普通 Heartbeat/quota 默认在 `agent_channel.work_context` 返回完整选定 Todo、
启用的验收与当前 Agent 相关的 User Todo，附带来源和修订，不需要开启 TurnEnvelope。
混合了其他 Todo 和历史证据的 Goal Markdown 文件保留为 `required_reads` 中的全文
读取命令：quota 核验来源可用性，不每轮自动灌入整个文件，也不按标题猜测哪些文字
可以替代原始 Goal。依赖交付前仍须读取；局部验收不能替代整体目标。
已附带的内容不要求重复读取；未提供的 provider 内容继续保留精确命令与顺序。
`work_context.complete` 只表示未发现来源失败，未完成的必读义务仍然有效。
偏好只在对应作用域有记录时出现；Explore 和偏好沿用各自 hook 的真实 adapter，
读取范围由 capability owner 决定。来源失败保留未完成必读项并阻止依赖交付，恢复后
重新准入；User gate 仍生效。大小告警不截断验收或移除剩余必读项。

`todo list --todo-id …` 默认只返回一份完整 `todo.text`，保留来源、修订、身份、
状态和关系。精确响应移除列表及角色视图，不保留兼容开关；`--thin` 只用于概览。
缺失/过滤返回未匹配，歧义/来源故障报错，不退回旧摘要。读到记录不等于获得执行
权限；阻塞、已完成、归档记录仍只是观察。需求改变须重新准入。

安装后重新生成旧展开式 heartbeat 提示；此 PR 不热更新 runtime/automation，
也不证明模型已采用或获得质量收益。回滚需恢复旧版本并重新生成提示。
