# 术语与命令入口

本附录面向带着实际问题回查的读者，不要求先完成源码课程。命令以当前安装的 `--help` 为准；本页给出入口和判断条件，不替代完整 CLI reference，也不自动授权 mutation。

## 先读取，再决定是否改变状态 {#read-before-change}

从已有配置确认精确 registry、runtime root、Goal 与 Todo，不从显示名称猜测，不使用教学 T1/M1 代号替代真实 id。下面命令不请求完成工作、获取租约或结算。先设置四个变量，缺失就停止：

```bash
: "${REGISTRY:?Set the existing registry path}"
: "${RUNTIME:?Set the existing runtime root}"
: "${GOAL:?Set the exact goal id}"
: "${TODO:?Set the exact todo id}"
loopx --registry "$REGISTRY" --runtime-root "$RUNTIME" --format json \
  todo list --goal-id "$GOAL" --todo-id "$TODO"
loopx --registry "$REGISTRY" --runtime-root "$RUNTIME" --format json \
  task-lease inspect --goal-id "$GOAL" --todo-id "$TODO"
```

读取可能启动托管 runtime，两次结果也不是一个原子快照。核对各自 identity、来源、时间和可用版本；缺失就报告缺失，不能补成 approved、open 或 zero。需要判断某次真实操作时，还要回到那次操作自己的回执。

`quota should-run --codex-app` 的相关准入路径可能创建 heartbeat receipt；`refresh-state` 是写回；获取/续租、结算和 scheduler ACK 各有执行条件。它们不属于上面两条只查看工作与租约的命令，不应在诊断脚本里无限重跑。

## 从症状找到负责入口 {#diagnostic-routing}

先找第一处缺少证据的关系，而不是清空整个 Goal。下表是查阅方法，不是新的自动修复状态机。

| 症状 | 先取得的事实 | 规则与操作入口 | 完成或停止条件 |
| --- | --- | --- | --- |
| 页面和 Todo 不同 | 同一对象的 source、模式、revision/时间与投影 | [状态](state-substrate.md)、[投影修复](04-runtime-boundaries.md#projection-repair) | 来源恢复并重读；不由页面接管 source |
| HTTP 成功却不知是否执行 | 原请求、operation receipt、当前 source | [Workspace 暂停案例](workspace-v1.md#pause-readback) | 确认该操作，而不凭状态差异猜因果 |
| 过去 acquire 成功，现在拒写 | 当前 owner/key/version、模式、Todo 状态 | [权限](work-graph-and-authority.md#authority-layers) | 取得合法当前证明；工作已结束则停止 |
| 外部请求超时 | 原 operation identity 与 provider readback | [恢复](04-runtime-boundaries.md#recovery-or-new-execution) | committed/absent 得到支持后处理；unknown 保留责任 |
| 写回存在但记录未齐 | 原 settlement 的状态与待完成动作 | [结算](03-one-turn.md#settlement-recovery) | 只完成尚欠记录，不重复产物或扣减 |
| 原 Turn 等待时出现独立工作 | 原 Turn 绑定、等待写回与后续候选 | [等待收口](03-one-turn.md#wait-closeout) | 先按当前版本合同收口，再让新准入选择 |
| Monitor 安静或反复 replan | due、观察目标、当前 lane、可选工作与 Host | [观察](04b-budget-and-admission.md#observation-owners) | 真实等待可解释，或恢复缺失的执行面 |
| 旧 CI 绿色，当前代码已改变 | 实际 artifact revision、受影响要求与证据 | [变化传播](04-runtime-boundaries.md#changed-evidence) | 当前结论有适用证据，不抹掉历史 |
| 多个分支各自通过但无法交付 | 各自产物、接收方采用版本和组合验证 | [交接与集成](work-graph-and-authority.md#handoff-to-integration) | 组合与接受责任闭合，不由单个分支代替 |
| 产物生成但收件人没收到 | 目标、route、发送/交付读回与授权 | [第一份交付](05-connect-existing-project.md#first-delivery) | 按任务要求完成返回或明确剩余责任 |

公开反馈只保留最小、可复核的版本、错误码和 public-safe 引用，不上传 live registry、凭据、raw transcript 或私有运行记录。证据不足时写明“尚不能判断什么、缺少哪项读回、由谁确认”。测试的损坏注入不属于真实项目的恢复操作。

## 要求一：状态能脱离上下文 {#requirement-one-state-outlives-context}

| 术语 | 阅读时应保留的区别 |
| --- | --- |
| Goal / Acceptance | 目标身份与可观察接受条件；不等于某条 Todo 的状态 |
| Source / Projection | 负责事实的来源与可重建的视图；legacy Markdown 的角色要按模式判断 |
| Evidence / Receipt | 支持结论的材料与已接受操作的记录；都需要正确绑定 |
| Kernel | 通用控制面的状态转换 owner，不替外部系统拥有真实世界 |

完整解释见[状态章节](state-substrate.md)。只存在于当前 prompt 的信息，不足以证明相应生命周期事实已持久化；保存了聊天，也不自动使其成为当前 authority。

## 要求二：中断停在可辨识位置 {#requirement-two-interruption-stops-at-identifiable-points}

Todo 是有身份的工作；frontier 是按当前约束得到的候选；Turn / operation identity 关联一次执行和恢复。历史 receipt 与新的执行资格不相同。artifact revision、provider revision、lease version/epoch 也不能互相替换，详见[版本依据](04-runtime-boundaries.md#revision-bases)。

## 要求三：明确当前执行者与作用范围 {#requirement-three-one-accountable-actor}

Agent identity 表达 lane，不证明 Host。Vision 表达适用的 per-Agent 路线。Claim 是归属；lease 在适用 writer/mode 下参与执行证明；Gate 表达具名决定范围；Host 提供实际执行与唤醒条件。这些机制不合成一个全系统唯一行动者，也不自动提供 OS sandbox。详见[工作图](work-graph-and-authority.md)。

## 要求四：消耗有上限且可外部观察 {#requirement-four-bounded-externally-observable-spend}

Quota 参与准入与内部记账；Monitor 保存目标和观察关系；scheduler/backoff 安排后续时机；Host 或已接入事件通道实际唤醒。Replan 改变路线而非只改变间隔。No-spend 不等于真实资源免费，见[观察责任](04b-budget-and-admission.md#observation-owners)。

## 四个要求之外的词 {#words-outside-the-four-requirements}

Capability 描述调用者可依赖的 outcome contract，Provider 提供实现或访问外部系统，Extension 管理独立包的安装、启停和版本生命周期。安装、doctor-ready、Goal 已配置与当前 Turn 有权使用是不同事实，见[放置规则](08-extension-placement.md)和[生命周期](10-extension-lifecycle.md)。

## 核心命令速查 {#core-command-quick-index}

| 需要做什么 | 入口 | 需要核对的限制 |
| --- | --- | --- |
| 核对安装与运行前提 | `loopx --version`、`loopx doctor` | 不证明某项实际交付已经成功 |
| 查看连接和状态 | `loopx registry`、`loopx status`、`loopx history` | 核对实际 registry / Goal，显示可能裁剪 |
| 精确查看 Todo | `loopx todo list --goal-id <goal-id> --todo-id <todo-id>` | 不从缺少显示推断不存在 |
| 压缩工作列表 | `loopx todo list --goal-id <goal-id> --thin --format json` | 有界显示不等于完整候选集 |
| 查看当前 lease | `loopx task-lease inspect` | 读回不是获取新执行证明 |
| 查看 Agent 证据 | `loopx history --goal-id <goal-id> --agent-id <agent-id> --limit 30` | 历史与当前事实分开 |
| 请求当前准入 | `loopx quota should-run` | 读完整 contract；相关 Host 路径可能记 receipt |
| 查看受管 Turn journal | `loopx turn inspect-journal` | 保留原 Turn key；诊断不执行恢复 |
| 配置与能力发现 | `loopx capability list/show`、`loopx configure-goal` | 无设置 flag 的读取与 execute 分开 |
| 查看独立包 | `loopx extension list --format json` | 可见不等于当前可运行 |

`v0.5.4` 新增的 `todo list --thin` 是显式启用的有界投影，不改变默认 list 的选择、排序、quota 或 lifecycle 语义。需要完整信息时精确读取，不修改显示上限来绕过业务检查。

## 项目接入与实际交付

```bash
loopx connect --dry-run
loopx start-goal --guided --project . \
  --goal-id <goal-id> --agent-id <agent-id> \
  --goal-text "<goal text>" --host-surface codex-app
```

这些是预览入口；`connect` 的实际写入与 packet 中后续动作必须分别确认。使用 CLI TUI 时选择 `codex-cli-tui`，不凭空选择一个 Host。省略身份参数可能进入 selection gate；已有 lane 不能当作默认 takeover。完整过程见[接入](05-connect-existing-project.md#three-completions)与[第一份交付](05-connect-existing-project.md#first-delivery)。

## Scheduler 收敛入口 {#scheduler-entry}

当 packet 要求 Host apply 时，应用其当前建议并读取真实结果，再执行该 packet 绑定的 ACK：

```text
loopx quota scheduler-ack-current <packet-bound-args...>
```

以上是命令形状，不是可直接复制的参数。使用完整 `ack_hint.cli_args`；apply 失败或结果未知时不伪造成功 ACK，按 `failure_hint.cli_args` 记录对应结果。已确认 Host 与目标 cadence 相同的路径可能只欠 ACK，不能为了生成回执重复无意义的更新。详见[预算与观察](04b-budget-and-admission.md)。

## 安全升级与回滚

```bash
loopx update check
loopx update plan
loopx update apply
loopx doctor
```

先确认安装 owner、目标版本与计划，再授权 apply。普通用户不把 `--ref main` 当成发布版升级默认。成功升级 CLI 不证明所有 Host、Provider 和已有 Goal 状态均已迁移；按实际使用面检查 skills、状态、Host readback 和 enabled Extension readiness。

需要备份时，先预览再执行：

```bash
loopx backup-state --project .
loopx backup-state --project . --execute
```

备份是私有恢复材料，可能包含敏感运行状态，不提交到项目。使用 `loopx update --rollback previous` 前，先读当前帮助并确认安装来源支持该恢复方式；回滚程序不等于回滚独立 package、已经写入的状态格式或外部效果。后者仍归各自 owner，见[恢复与补偿](04-runtime-boundaries.md)。

## Extension 生命周期

```text
init → 安装 package → install preview → install --execute → doctor/readback
     → 有效输入下 run → disable/enable → upgrade/rollback → 再读回
```

不重复安装已安装的 Extension。使用独立 state file 与同一 Python 环境完成[完整 scaffold](09-extension-scaffold.md)和[生命周期练习](10-extension-lifecycle.md)。`rollback_available=false` 与“有回滚目标但 doctor 失败”是不同问题；保留旧 activation 记录也不保证原 entrypoint 仍可执行。

## 核心协议索引

| 主题 | 主要入口 |
| --- | --- |
| Goal、身份与 Host 启动 | [Goal command](/loopx/docs/reference/protocols/loopx-goal-command-v0/) |
| Source / projection 与长期工作 | [Long-horizon state](/loopx/docs/reference/protocols/long-horizon-agent-state-protocol-v0/) |
| Agent-scoped 历史 | [Evidence ledger](/loopx/docs/reference/protocols/agent-scoped-evidence-ledger-v0/) |
| 已退役的 Todo event API | [Retired event source](/loopx/docs/reference/protocols/event-sourced-state-contract-v0/) |
| Active-state 读模型 | [Structured projection](/loopx/docs/reference/protocols/active-state-structured-projection-v0/) |
| 工作图、Gate 与 peer | [Task graph](/loopx/docs/reference/protocols/task-graph-projection-v0/)、[Decision scope](/loopx/docs/reference/protocols/decision-scope-v0/)、[Peer runtime](/loopx/docs/reference/protocols/peer-agent-runtime-v1/) |
| Vision 与改路 | [Vision / replan](/loopx/docs/reference/protocols/goal-vision-replan-contract-v0/) |
| 显式受管单轮 | [LoopX Turn](/loopx/docs/reference/protocols/loopx-turn-v0/)、[Turn envelope](/loopx/docs/reference/protocols/turn-envelope-v0/) |
| Host、Session 与写回 | [Host integration](/loopx/docs/reference/protocols/host-integration-surface-v0/)、[Session projection](/loopx/docs/reference/protocols/session-runtime-loopx-projection-v0/)、[Controlled writeback](/loopx/docs/reference/protocols/session-runtime-controlled-writeback-v0/) |
| 并发写入依据 | [Local write correctness](/loopx/docs/reference/protocols/local-state-write-correctness-v0/) |

协议页也有版本和实施状态；Accepted RFC、实验合同与已发布行为不能互相替代。源码进阶比较使用固定提交，实际运行先检查自己的 checkout 是否包含对应实现。准备修改规则时再进入[Course 练习](12-control-plane-course.md#reader-checkpoints)与[贡献路线](source-protocol-map.md)。
